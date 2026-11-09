#####################################################################
# COD2BOT - Call of Duty 2 community bot for Eggdrop 1.9 (Tcl 8.6)
#
# Thin, non-blocking IRC layer. All the logic (news, tournaments,
# servers, clans, wars...) lives in the Python backend, reached through
# an asynchronous HTTP call on localhost: a slow website or a stopped
# backend never freezes the bot.
#
# Install:  source scripts/cod2bot/cod2bot.tcl   (in eggdrop.conf)
#           .chanset #yourchannel +cod2          (partyline)
#####################################################################

package require Tcl 8.5
package require http

namespace eval ::cod2bot {
	variable version "1.0.0"
	variable basedir [file dirname [file normalize [info script]]]

	# defaults (overridden by cod2bot.conf)
	variable api "http://127.0.0.1:8765"
	variable tokenfile "cod2bot/data/api.token"
	variable prefix "!"
	variable private 1
	variable flag_owner "n"
	variable flag_admin "m"
	variable flag_moderator "o|o"
	variable chanop_is_moderator 0
	variable user_max 5
	variable user_window 30
	variable chan_max 8
	variable chan_window 30
	variable ignore_time 60
	variable max_chan_lines 5
	variable max_notice_lines 12
	variable nocolor_chans {}
	variable poll 15
	variable timeout 15000
	variable yield_to_service 1
	variable reserved {tcl exec error}
	variable utf8_fix 0
	variable enable_chans {}

	# runtime state
	variable token ""
	variable commands {}
	variable online 0
	variable backend_version "?"
	variable last_down_notice 0
	variable polling 0
	if {![info exists usercalls]} { variable usercalls; array set usercalls {} }
	if {![info exists chancalls]} { variable chancalls; array set chancalls {} }
	if {![info exists ignored]} { variable ignored; array set ignored {} }

	set conf [file join $basedir cod2bot.conf]
	if {[file exists $conf]} {
		if {[catch {source $conf} err]} {
			putlog "cod2bot: error in $conf: $err"
		}
	} else {
		putlog "cod2bot: $conf not found, using defaults"
	}
}

setudef flag cod2

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

proc ::cod2bot::load_token {} {
	variable tokenfile
	variable token
	set path $tokenfile
	if {[file pathtype $path] ne "absolute"} {
		set path [file normalize [file join [pwd] $path]]
	}
	if {[catch {
		set fh [open $path r]
		set token [string trim [read $fh]]
		close $fh
	} err]} {
		set token ""
		putlog "cod2bot: cannot read token file $path ($err) - start the backend first"
		return 0
	}
	return [expr {$token ne ""}]
}

# Permission level sent to the backend: 0 user, 1 registered, 2 moderator, 3 admin, 4 owner
proc ::cod2bot::level {nick hand chan} {
	variable flag_owner
	variable flag_admin
	variable flag_moderator
	variable chanop_is_moderator
	if {$hand ne "*" && $hand ne "" && [validuser $hand]} {
		if {[matchattr $hand $flag_owner]} { return 4 }
		if {[matchattr $hand $flag_admin]} { return 3 }
		if {$chan ne "" && [validchan $chan]} {
			if {[matchattr $hand $flag_moderator $chan]} { return 2 }
		} elseif {[matchattr $hand [lindex [split $flag_moderator |] 0]]} {
			return 2
		}
		return 1
	}
	if {$chanop_is_moderator && $chan ne "" && [validchan $chan] && [isop $nick $chan]} { return 2 }
	return 0
}

# Sliding-window limiter: returns 1 if allowed
proc ::cod2bot::allow {arrayname key max window} {
	upvar #0 ::cod2bot::$arrayname calls
	set now [clock seconds]
	set kept {}
	if {[info exists calls($key)]} {
		foreach t $calls($key) {
			if {$now - $t < $window} { lappend kept $t }
		}
	}
	if {[llength $kept] >= $max} {
		set calls($key) $kept
		return 0
	}
	lappend kept $now
	set calls($key) $kept
	return 1
}

proc ::cod2bot::colours_ok {chan} {
	variable nocolor_chans
	if {$chan eq "" || ![validchan $chan]} { return 1 }
	foreach c $nocolor_chans {
		if {[string equal -nocase $c $chan]} { return 0 }
	}
	# channel mode +c strips colours: let the backend send plain text
	set modes [lindex [split [getchanmode $chan]] 0]
	if {[string match "*c*" $modes]} { return 0 }
	return 1
}

proc ::cod2bot::out {text} {
	variable utf8_fix
	if {$utf8_fix} { return [encoding convertto utf-8 $text] }
	return $text
}

# Would service.tcl answer this command for this user? (avoid double replies)
proc ::cod2bot::service_owns {hand cmd} {
	variable yield_to_service
	variable prefix
	if {!$yield_to_service || $hand eq "*" || ![validuser $hand]} { return 0 }
	if {![info exists ::service::cmdmap([string tolower $cmd])]} { return 0 }
	set trig [getuser $hand XTRA mytrigger]
	if {$trig eq ""} { set trig [expr {[info exists ::service::trigger] ? $::service::trigger : "\$"}] }
	return [string equal $trig $prefix]
}

# ---------------------------------------------------------------------------
# HTTP (always asynchronous)
# ---------------------------------------------------------------------------

proc ::cod2bot::request {method path query callback} {
	variable api
	variable token
	variable timeout
	if {$token eq "" && ![load_token]} {
		{*}$callback 0 ""
		return
	}
	set url "$api$path"
	set opts [list -timeout $timeout -headers [list X-COD2BOT-Token $token] \
		-command [list ::cod2bot::response $callback]]
	if {$method eq "POST"} {
		lappend opts -query $query -type "application/x-www-form-urlencoded; charset=utf-8"
	} elseif {$query ne ""} {
		append url "?$query"
	}
	if {[catch {::http::geturl $url {*}$opts} err]} {
		# connection refused is reported here immediately
		set_online 0 $err
		{*}$callback 0 ""
	}
}

proc ::cod2bot::response {callback tok} {
	set status [::http::status $tok]
	set code [::http::ncode $tok]
	set data [::http::data $tok]
	::http::cleanup $tok
	if {$status ne "ok" || $code != 200} {
		if {$code == 401} {
			putlog "cod2bot: backend refused the token (401) - check $::cod2bot::tokenfile"
			set ::cod2bot::token ""
		}
		set_online 0 "status=$status code=$code"
		{*}$callback 0 ""
		return
	}
	set_online 1
	{*}$callback 1 $data
}

proc ::cod2bot::set_online {state {why ""}} {
	variable online
	if {$state != $online} {
		if {$state} {
			putlog "cod2bot: backend online"
		} else {
			putlog "cod2bot: backend offline ($why)"
		}
	}
	set online $state
}

# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

proc ::cod2bot::pubm {nick uhost hand chan text} {
	if {![channel get $chan cod2]} { return 0 }
	handle $nick $uhost $hand $chan $text
	return 0
}

proc ::cod2bot::msgm {nick uhost hand text} {
	variable private
	variable prefix
	variable commands
	if {!$private} { return 0 }
	# Private lines can be Eggdrop's own msg commands (ident, pass, op...):
	# only known COD2BOT commands are forwarded, never anything else.
	if {![llength $commands]} { return 0 }
	set first [string tolower [lindex [split [string trimleft $text $prefix]] 0]]
	if {[lsearch -exact $commands $first] == -1} { return 0 }
	if {[string index $text 0] ne $prefix} {
		# "news" without prefix: only if no Eggdrop msg command has that name
		foreach b [binds msg] {
			if {[string equal -nocase [lindex $b 2] $first]} { return 0 }
		}
		set text "$prefix$text"
	}
	handle $nick $uhost $hand "" $text
	return 0
}

proc ::cod2bot::handle {nick uhost hand chan text} {
	variable prefix
	variable commands
	variable reserved
	variable user_max
	variable user_window
	variable ignored
	variable ignore_time
	variable online
	variable last_down_notice
	set text [string trim $text]
	if {[string index $text 0] ne $prefix || [string length $text] < 2} { return }
	set words [split [string range $text 1 end]]
	set cmd [string tolower [lindex $words 0]]
	set args [join [lrange $words 1 end]]
	if {![regexp {^[a-z0-9][a-z0-9_-]{0,31}$} $cmd]} { return }
	if {[lsearch -exact $reserved $cmd] != -1} { return }
	if {[llength $commands] && [lsearch -exact $commands $cmd] == -1} { return }
	if {[service_owns $hand $cmd]} { return }

	set key [string tolower $uhost]
	if {[info exists ignored($key)]} {
		if {[clock seconds] < $ignored($key)} { return }
		unset ignored($key)
	}
	set lvl [level $nick $hand $chan]
	if {$lvl < 2 && ![allow usercalls $key $user_max $user_window]} {
		set ignored($key) [expr {[clock seconds] + $ignore_time}]
		putlog "cod2bot: ignoring $nick!$uhost for ${ignore_time}s (command flood)"
		puthelp "NOTICE $nick :Slow down - ignored for ${ignore_time}s."
		return
	}
	set query [::http::formatQuery nick $nick host $uhost hand $hand chan $chan level $lvl \
		cmd $cmd args $args colours [colours_ok $chan]]
	request POST /cmd $query [list ::cod2bot::reply $nick $chan $cmd]
}

proc ::cod2bot::reply {nick chan cmd ok data} {
	variable max_chan_lines
	variable max_notice_lines
	variable chan_max
	variable chan_window
	variable last_down_notice
	if {!$ok} {
		# tell the user at most once a minute
		if {[clock seconds] - $last_down_notice > 60} {
			set last_down_notice [clock seconds]
			puthelp "NOTICE $nick :\00304COD2BOT\003: backend unavailable, try again in a moment."
		}
		return
	}
	set target [expr {$chan eq "" ? $nick : $chan}]
	set nchan 0
	set nnotice 0
	foreach line [split $data \n] {
		set kind [string index $line 0]
		set text [string range $line 2 end]
		if {$text eq ""} { continue }
		if {$kind eq "C" && $nchan < $max_chan_lines} {
			if {$chan ne "" && ![allow chancalls [string tolower $chan] $chan_max $chan_window]} {
				# channel is busy: send the rest privately
				set kind "N"
			} else {
				puthelp "PRIVMSG $target :[out $text]"
				incr nchan
				continue
			}
		}
		if {$nnotice < $max_notice_lines} {
			puthelp "NOTICE $nick :[out $text]"
			incr nnotice
		}
	}
}

# ---------------------------------------------------------------------------
# announcements (pulled from the backend)
# ---------------------------------------------------------------------------

proc ::cod2bot::announce_channels {} {
	set list {}
	foreach chan [channels] {
		if {[channel get $chan cod2] && [botonchan $chan]} { lappend list $chan }
	}
	return $list
}

proc ::cod2bot::poll {} {
	variable poll
	variable polling
	# reschedule first: an error below never stops the loop
	foreach t [utimers] {
		if {[lindex $t 1] eq "::cod2bot::poll"} { killutimer [lindex $t 2] }
	}
	utimer $poll ::cod2bot::poll
	if {$polling} { return }
	set chans [announce_channels]
	if {![llength $chans] || ![server_ready]} { return }
	set polling 1
	request GET /announce [::http::formatQuery channels [join $chans ,]] ::cod2bot::announced
}

proc ::cod2bot::server_ready {} {
	return [expr {[info exists ::server-online] && ${::server-online} != 0}]
}

proc ::cod2bot::announced {ok data} {
	variable polling
	set polling 0
	if {!$ok} { return }
	foreach line [split $data \n] {
		set tab [string first \t $line]
		if {$tab < 1} { continue }
		set chan [string range $line 0 [expr {$tab - 1}]]
		set text [string range $line [expr {$tab + 1}] end]
		if {![validchan $chan] || ![botonchan $chan] || ![channel get $chan cod2]} { continue }
		if {![colours_ok $chan]} { set text [stripcodes bcruag $text] }
		puthelp "PRIVMSG $chan :[out $text]"
	}
}

# ---------------------------------------------------------------------------
# health / command list
# ---------------------------------------------------------------------------

proc ::cod2bot::refresh {} {
	foreach t [timers] {
		if {[lindex $t 1] eq "::cod2bot::refresh"} { killtimer [lindex $t 2] }
	}
	timer 10 ::cod2bot::refresh
	request GET /commands "" ::cod2bot::got_commands
	request GET /health "" ::cod2bot::got_health
}

proc ::cod2bot::got_commands {ok data} {
	variable commands
	if {$ok && [string trim $data] ne ""} {
		set commands [split [string trim $data]]
	}
}

proc ::cod2bot::got_health {ok data} {
	variable backend_version
	if {$ok} { set backend_version [lindex [split $data] 1] }
}

# ---------------------------------------------------------------------------
# partyline: .cod2 status
# ---------------------------------------------------------------------------

proc ::cod2bot::dcc {hand idx text} {
	variable online
	variable backend_version
	variable commands
	variable version
	switch -- [string tolower [lindex [split $text] 0]] {
		"reload" {
			load_token
			refresh
			putdcc $idx "cod2bot: token reloaded, refreshing command list."
		}
		default {
			putdcc $idx "cod2bot $version | backend: [expr {$online ? "online v$backend_version" : "OFFLINE"}] | commands: [llength $commands] | channels: [join [announce_channels] {, }]"
			putdcc $idx "Enable a channel with: .chanset #channel +cod2   |   .cod2 reload"
		}
	}
	return 1
}

# ---------------------------------------------------------------------------
# binds / start
# ---------------------------------------------------------------------------

bind pubm - * ::cod2bot::pubm
bind msgm - * ::cod2bot::msgm
bind dcc m cod2 ::cod2bot::dcc
bind evnt - init-server ::cod2bot::evnt_connect
proc ::cod2bot::evnt_connect {type} { ::cod2bot::refresh }

foreach ::cod2bot::c $::cod2bot::enable_chans {
	if {[validchan $::cod2bot::c] && ![channel get $::cod2bot::c cod2]} {
		channel set $::cod2bot::c +cod2
		putlog "cod2bot: enabled +cod2 on $::cod2bot::c"
	}
}
unset -nocomplain ::cod2bot::c

::cod2bot::load_token
::cod2bot::refresh
::cod2bot::poll
putlog "cod2bot.tcl v$::cod2bot::version loaded (api $::cod2bot::api, prefix $::cod2bot::prefix) - .chanset #channel +cod2"

# Minimal Eggdrop simulation for testing scripts/cod2bot/cod2bot.tcl with tclsh.
# Implements only the commands the script uses.

set ::botnick "COD2BOT"
set ::server-online 1
set ::out {}
array set ::binds {}
array set ::udef {}
array set ::chanflag {}
array set ::chanmode {}
array set ::users {}
set ::utimers {}
set ::timers {}

proc putlog {text} { lappend ::log $text }
proc putdcc {idx text} { lappend ::out "DCC $text" }
proc puthelp {text} { lappend ::out $text; set ::got_output 1 }
proc putserv {text} { lappend ::out $text; set ::got_output 1 }
proc bind {type flags mask proc} { lappend ::binds($type) [list $flags $mask $proc] }
proc binds {type} {
	set res {}
	if {[info exists ::binds($type)]} {
		foreach b $::binds($type) { lappend res [list $type [lindex $b 0] [lindex $b 1] 0 [lindex $b 2]] }
	}
	return $res
}
proc setudef {type name} { set ::udef($name) $type }
proc channels {} { return [lsort [array names ::chanmode]] }
proc validchan {c} { return [info exists ::chanmode([string tolower $c])] }
proc botonchan {c} { return [validchan $c] }
proc getchanmode {c} { return $::chanmode([string tolower $c]) }
proc channel {sub chan args} {
	set key "[string tolower $chan],[lindex $args 0]"
	if {$sub eq "get"} { return [expr {[info exists ::chanflag($key)] ? $::chanflag($key) : 0}] }
	if {$sub eq "set"} { set ::chanflag($key) 1 }
}
proc validuser {h} { return [info exists ::users([string tolower $h])] }
proc matchattr {hand flags {chan ""}} {
	if {![validuser $hand]} { return 0 }
	set have $::users([string tolower $hand])
	set global [lindex [split $flags |] 0]
	foreach f [split $global ""] { if {[string first $f $have] >= 0} { return 1 } }
	return 0
}
proc isop {nick chan} { return [string match "@*" $nick] }
proc getuser {hand type {key ""}} {
	if {[info exists ::xtra([string tolower $hand],$key)]} { return $::xtra([string tolower $hand],$key) }
	return ""
}
proc utimer {secs cmd} { lappend ::utimers [list $secs $cmd timer[incr ::tid]] ; return timer$::tid }
proc utimers {} { return $::utimers }
proc killutimer {id} {
	set keep {}
	foreach t $::utimers { if {[lindex $t 2] ne $id} { lappend keep $t } }
	set ::utimers $keep
}
proc timer {mins cmd} { lappend ::timers [list $mins $cmd timer[incr ::tid]] ; return timer$::tid }
proc timers {} { return $::timers }
proc killtimer {id} {
	set keep {}
	foreach t $::timers { if {[lindex $t 2] ne $id} { lappend keep $t } }
	set ::timers $keep
}
proc stripcodes {flags text} { return [regsub -all {\x03(\d{1,2}(,\d{1,2})?)?|[\x02\x0f\x16\x1d\x1f]} $text ""] }

# --- helpers for the tests -------------------------------------------------
proc addchan {chan modes {cod2 1}} {
	set ::chanmode([string tolower $chan]) $modes
	if {$cod2} { set ::chanflag([string tolower $chan],cod2) 1 }
}
proc adduser {hand flags} { set ::users([string tolower $hand]) $flags }

# fire every pubm/msgm bind like Eggdrop would
proc say {nick uhost hand chan text} {
	foreach b $::binds(pubm) { [lindex $b 2] $nick $uhost $hand $chan $text }
}
proc msg {nick uhost hand text} {
	foreach b $::binds(msgm) { [lindex $b 2] $nick $uhost $hand $text }
}

# wait until output arrives (or timeout ms)
proc wait_output {{ms 4000}} {
	set ::got_output 0
	set id [after $ms {set ::got_output timeout}]
	vwait ::got_output
	after cancel $id
	# collect lines arriving in the same burst
	after 150 {set ::settle 1}
	vwait ::settle
	return $::got_output
}
proc wait_ms {ms} { after $ms {set ::slept 1}; vwait ::slept }

# Integration test: cod2bot.tcl in a simulated Eggdrop against a running backend.
# Usage: tclsh test_cod2bot.tcl <dir with cod2bot.tcl + cod2bot.conf>
set here [file dirname [file normalize [info script]]]
source [file join $here mock_eggdrop.tcl]
set dir [lindex $argv 0]
set failures 0
proc check {name cond} {
	if {[uplevel 1 [list expr $cond]]} {
		puts "ok   $name"
	} else {
		puts "FAIL $name"
		puts "     output: $::out"
		incr ::failures
	}
}
proc lines_matching {pattern} {
	set res {}
	foreach l $::out { if {[string match $pattern $l]} { lappend res $l } }
	return $res
}

addchan #cod2bot "+Ctnl 1337"
addchan #nocolour "+tnc"
addchan #other "+tn" 0
adduser boss "jlmnoptx"
adduser regular "f"
bind msg - help *msg:help

source [file join $dir cod2bot.tcl]
wait_ms 1500
check "command list loaded from backend" {[llength $::cod2bot::commands] > 30}
check "backend online" {$::cod2bot::online == 1}
# the first announcement poll runs when the script loads
check "announcement delivered to +cod2 channel" {[llength [lines_matching "PRIVMSG #cod2bot :*TEST ANNOUNCEMENT*"]] == 1}
check "announcement stripped on +c channel" {[llength [lines_matching "PRIVMSG #nocolour :*TEST ANNOUNCEMENT*"]] == 1 && [string first \x03 [lindex [lines_matching "PRIVMSG #nocolour :*"] 0]] == -1}
check "no announcement in channel without +cod2" {[llength [lines_matching "PRIVMSG #other*"]] == 0}
set ann [lindex [lines_matching "PRIVMSG #cod2bot :*TEST ANNOUNCEMENT*"] 0]
set want [format "%c %c %c end" 0xB7 0xF1 0xBB]
check "UTF-8 decoded once (no mojibake)" {[string first $want $ann] >= 0 && [string first [format %c 0xC2] $ann] == -1 && [string first [format %c 0xC3] $ann] == -1}
check "multi-line announcement delivered in order" {[lsearch -glob $::out "PRIVMSG #cod2bot :SECOND LINE*"] == [lsearch -glob $::out "PRIVMSG #cod2bot :*TEST ANNOUNCEMENT*"] + 1}

set ::out {}
say Bob bob@host1 * #cod2bot "!map toujane"
wait_output
set l [lines_matching "PRIVMSG #cod2bot :*"]
check "channel reply with colours" {[llength $l] == 1 && [string match "*Toujane*" [lindex $l 0]] && [string first \x03 [lindex $l 0]] >= 0}

set ::out {}
say Bob bob@host2 * #nocolour "!map toujane"
wait_output
set l [lines_matching "PRIVMSG #nocolour :*"]
check "+c channel gets plain text" {[llength $l] == 1 && [string first \x03 [lindex $l 0]] == -1}

set ::out {}
say Bob bob@host3 * #other "!map toujane"
wait_ms 600
check "channel without +cod2 ignored" {[llength $::out] == 0}

set ::out {}
say Bob bob@host3 * #cod2bot "!tcl puts hacked"
say Bob bob@host3 * #cod2bot "!notacommand"
say Bob bob@host3 * #cod2bot "hello !map"
wait_ms 600
check "reserved/unknown/non-prefixed ignored" {[llength $::out] == 0}

set ::out {}
say Bob bob@host4 * #cod2bot "!help"
wait_output
check "help goes by notice" {[llength [lines_matching "NOTICE Bob :*"]] >= 1 && [llength [lines_matching "PRIVMSG*"]] == 0}

set ::out {}
msg Bob bob@host5 * "map burgundy"
wait_output
check "private command answered in private" {[llength [lines_matching "PRIVMSG Bob :*Burgundy*"]] == 1}

set ::out {}
msg Bob bob@host5 * "help"
msg Bob bob@host5 * "ident secretpassword"
wait_ms 600
check "private: Eggdrop msg commands not forwarded" {[llength $::out] == 0}

set ::out {}
msg Bob bob@host5 * "!help"
wait_output
check "private: prefixed !help reaches COD2BOT" {[llength [lines_matching "NOTICE Bob :*"]] >= 1}

set ::out {}
say Boss boss@owner boss #cod2bot "!maintenance"
wait_output
check "owner level from Eggdrop flags" {[llength [lines_matching "*Maintenance*"]] == 1}

set ::out {}
say Joe joe@reg regular #cod2bot "!broadcast hi"
wait_output
check "registered user cannot broadcast" {[llength [lines_matching "*needs level admin*"]] == 1}

# service.tcl coexistence: user with service trigger "!" -> service.tcl answers !version
namespace eval ::service { variable trigger "\$" ; array set cmdmap {version x help x} }
set ::xtra(regular,mytrigger) "!"
set ::out {}
say Joe joe@reg2 regular #cod2bot "!version"
wait_ms 600
check "yields !version to service.tcl" {[llength $::out] == 0}
set ::out {}
say Joe joe@reg3 regular #cod2bot "!cod2 version"
wait_output
check "!cod2 version always works" {[llength [lines_matching "PRIVMSG #cod2bot :*COD2BOT*v*"]] == 1}

# flood: 5 commands in 30s allowed, the 6th ignores the user
set ::out {}
for {set i 0} {$i < 7} {incr i} { say Flood flood@x * #cod2bot "!ping" }
wait_output
wait_ms 800
check "flooder warned and ignored" {[llength [lines_matching "NOTICE Flood :Slow down*"]] == 1}
check "flooder got at most 5 answers" {[llength [lines_matching "PRIVMSG #cod2bot :*pong*"]] <= 5}

# announcements are sent only once
set ::out {}
::cod2bot::poll
wait_ms 800
check "announcement not repeated" {[llength [lines_matching "*TEST ANNOUNCEMENT*"]] == 0}
check "poll timer rescheduled (single timer)" {[llength [lsearch -all -index 1 $::utimers ::cod2bot::poll]] == 1}

# backend down
set ::cod2bot::api "http://127.0.0.1:1"
set ::cod2bot::last_down_notice 0
set ::out {}
say Bob bob@host9 * #cod2bot "!map toujane"
wait_output
check "backend down -> friendly notice" {[llength [lines_matching "NOTICE Bob :*backend unavailable*"]] == 1}
check "marked offline" {$::cod2bot::online == 0}

puts "failures: $failures"
exit [expr {$failures > 0}]

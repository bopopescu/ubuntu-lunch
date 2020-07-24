#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Lunch
# Copyright (C) 2009 Société des arts technologiques (SAT)
# http://www.sat.qc.ca
# All rights reserved.
#
# This file is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# Lunch is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Lunch.  If not, see <http://www.gnu.org/licenses/>.
"""
A L{lunch.commands.Command} is the interface to what command line the user wants to starts. 

Once given to a L{lunch.main.Main}, each command starts a lunch-subordinate process, communicating with it via its stdin and stdout. 
The lunch-subordinate is then told which command to launch and asked to launch it, and stop it, on-demand. 

Author: Alexandre Quessy <alexandre@quessy.net>
"""
import os
import stat
import time
import logging
import warnings

from twisted.internet import defer
from twisted.internet import error
from twisted.internet import protocol
from twisted.internet import reactor
from twisted.internet import task
from twisted.internet import utils
from twisted.python import failure
#from twisted.python import log
from twisted.python import logfile
from twisted.python import procutils

from lunch import sig
from lunch import graph
from lunch.states import *
from lunch import logger

log = logger.start(name='commands')

def run_and_wait(executable, *arguments):
    """
    Runs a command and trigger its deferred with the output when done.
    Returns a deferred.
    """
    # TODO: use it.
    try:
        executable = procutils.which(executable)[0]
    except IndexError:
        msg = "Could not find executable %s" % (executable)
        return failure.Failure(RuntimeError(msg))
    d = utils.getProcessOutput(executable, arguments)
    def cb(result, executable, arguments):
        print 'Call to %s %s returned.\nResult: %s\n' % (executable, arguments, result)
        return result
    def eb(reason, executable, arguments):
        print 'Calling %s %s failed.\nError: %s' % (executable, arguments, reason)
        return reason
    d.addCallback(cb, executable, list(arguments))
    d.addErrback(eb, executable, list(arguments))
    return d

class SubordinateProcessProtocol(protocol.ProcessProtocol):
    """
    Process of a lunch-subordinate. (through SSH, or directly bash)
 
    The Lunch Main controls it by its stdin and monitors it with its stdout.
    """
    def __init__(self, command):
        """
        @param command: L{Command} instance.
        """
        self.command = command

    def connectionMade(self):
        """
        Called once the process is started.
        """
        self.command._on_connection_made()

    def outReceived(self, data):
        """
        Called when text is received from the lunch-subordinate process stdout

        Twisted will not splitlines, it gives an arbitrary amount of
        data at a time. This way, our manager only gets one line at 
        a time.
        """
        for line in data.splitlines():
            if line != "":
                self.command._received_message(line)

    def errReceived(self, data):
        """
        Called when text is received from the lunch-subordinate process stderr
        """
        for line in data.splitlines().strip():
            if line != "":
                log.debug("stderr: " + line + "\n")

    def processEnded(self, reason):
        """
        Called when the lunch-subordinate process has exited.
        status is probably a twisted.internet.error.ProcessTerminated
        "A process has ended with a probable error condition: process ended by signal 1"
        
        This is called when all the file descriptors associated with the child 
        process have been closed and the process has been reaped. This means it 
        is the last callback which will be made onto a ProcessProtocol. 
        The status parameter has the same meaning as it does for processExited.
        """
        exit_code = reason.value.exitCode
        log.info("Subordinate %s process ended with %s." % (self.command.identifier, exit_code))
        self.command._on_process_ended(reason.value.exitCode)
    
    def inConnectionLost(self):
        pass #log.msg("Subordinate stdin has closed.")

    def outConnectionLost(self):
        pass #log.msg("Subordinate stdout has closed.")
    
    def errConnectionLost(self):
        pass #log.msg("Subordinate stderr has closed.")

    def processExited(self, reason):
        """
        This is called when the lunch-subordinate process has been reaped, and receives 
        information about the process' exit status. The status is passed in the form 
        of a Failure instance, created with a .value that either holds a ProcessDone 
        object if the process terminated normally (it died of natural causes instead 
        of receiving a signal, and if the exit code was 0), or a ProcessTerminated 
        object (with an .exitCode attribute) if something went wrong.
        """
        exit_code = reason.value.exitCode
        log.info("process has exited : %s." % (str(exit_code)))
    
class Command(object):
    """
    Manages a lunch-subordinate process, telling it which child process to start.

    Uses a SubordinateProcessProtocol, which in turn controls a lunch-subordinate process.
    This is also what the programmer uses to control a child process.
    """
    #TODO: add gid
    #TODO: allow to switch uid (when running on localhost)
    #TODO: add delay_kill (to the add_command "private" function)
    #TODO: add clear_old_logs
    #TODO: add time_started
    #TODO: add enabled. (for respawning or not a process, without changing its respawn attribute.
    #TODO: move send_* and recv_* methods to the SubordinateProcessProtocol.
    #TODO: add wait_returned attribute. (commands after which we should wait them to end before calling next)
    
    def __init__(self, command=None, identifier=None, env=None, user=None, host=None, order=None, sleep_after=0.25, respawn=True, minimum_lifetime_to_respawn=0.5, log_dir=None, depends=None, verbose=False, try_again_delay=0.25, give_up_after=0, enabled=None, delay_before_kill=8.0, ssh_port=None):
        """
        @param command: Shell string. The first item is the name of the name of the executable.
        @param depends: Commands to which this command depends on. List of strings.
        @param enabled: Whether it's enabled or not.
        @param env: dict with environment variables to set for the process to run.
        @param host: Host name or IP address, if spawned over SSH.
        @param identifier: Any string. Used as a file name, so avoid spaces and exotic characters.
        @param log_dir: Full path to the directory to save log files in.
        @param minimum_lifetime_to_respawn: Minimum time a process must have lasted to be respawned.
        @param respawn: Set to False if this is a command that must be ran only once.
        @param sleep_after: How long to wait before launching next command after this one.
        @param delay_before_kill: Time to wait between sending SIGTERM and SIGKILL signals when it's time to stop the child process.
        @param user: User name, if spawned over SSH.
        @param verbose: Prints more information if set to True.
        @param ssh_port: SSH port to use. 
        @type command: str
        @type depends: list
        @type enabled: bool
        @type env: dict
        @type host: str
        @type identifier: str
        @type log_dir: str
        @type minimum_lifetime_to_respawn: float
        @type respawn: bool
        @type sleep_after: float
        @type user: str
        @type verbose: bool
        @type delay_before_kill: float
        @type ssh_port: int
        @param try_again_delay: Time to wait before trying again if it crashes at startup.
        @type try_again_delay: C{float}
        @param give_up_after: How many times to try again before giving up.
        @type give_up_after: C{int}
        """
        self.command = command
        self.identifier = identifier
        self.env = {}
        if env is not None:
            self.env.update(env)
        self.user = user
        self.host = host
        self.ssh_port = ssh_port
        self.order = order
        self.sleep_after = sleep_after
        self.respawn = respawn
        self.enabled = True 
        if enabled is not None:
            self.enabled = enabled
        self.to_be_deleted = False
        self.depends = depends
        self.how_many_times_run = 0
        self.how_many_times_tried = 0
        self.delay_before_kill = delay_before_kill
        self.verbose = verbose
        self.retval = 0
        self.gave_up = False
        self.number_of_lines_received_from_subordinate = 0
        self._has_shown_ssh_error = False 
        self._has_shown_notfound_error = False 
        self.try_again_delay = try_again_delay
        self._current_try_again_delay = try_again_delay # doubles up each time we try
        self._next_try_time = 0
        self._received_ready = False
        self._previous_launching_time = 0
        self.give_up_after = give_up_after # 0 means infinity of times
        self.minimum_lifetime_to_respawn = minimum_lifetime_to_respawn #FIXME: rename
        self._quit_subordinate_deferred = None
        if log_dir is None:
            log_dir = "/var/tmp/lunch"# XXX Overriding the child's log dir.
            # XXX: used to be something like:
            #SLAVE_LOG_SUBDIR = "lunch_log"
            #subordinate_log_dir = os.path.join(os.getcwd(), SLAVE_LOG_SUBDIR)
        self.child_log_dir = log_dir # for both lunch-subordinate and child. If not set, defaults to $PWD/lunch_log
        self.subordinate_log_dir = self.child_log_dir
        # ------- private attributes:
        self.subordinate_state = STATE_STOPPED # state of the lunch-subordinate process, not the child process that the lunch-subordinate handles
        self.child_state = STATE_STOPPED # state of the child process that the lunch-subordinate handles.
        self.subordinate_state_changed_signal = sig.Signal() # params: self, new_state
        self.child_state_changed_signal = sig.Signal() # params: self, new_state
        self.child_pid_changed_signal = sig.Signal() # params: self, new_pid
        self.command_not_found_signal = sig.Signal() # params: self, command
        self.ssh_error_signal = sig.Signal() # params: self, error_message
        if command is None:
            raise RuntimeError("You must provide a command to be run.")
        log.info("Creating command %s ($ %s) on %s@%s" % (self.identifier, self.command, self.user, self.host))
            #self.send_stop()
        self._process_protocol = None
        self._process_transport = None
        # Some attributes might be changed by the main, namely identifier and host.
        # That's why we sait until start() is called to initiate the subordinate_logger.
        self.subordinate_logger = None
        self.child_pid = None

    def is_ready_to_be_started(self):
        # self.enabled
        ret = self._next_try_time <= time.time() and self.child_state == STATE_STOPPED
        if ret and self.subordinate_state == STATE_RUNNING:
            if not self._received_ready:
                # log.debug("Not ready to start child %s since we did not receive the ready message." % (self))
                ret = False
        return ret
    
    def _start_logger(self):
        """
        Creates a log file for the lunch-subordinate's stdout.
        """
        if self.subordinate_logger is None:
            # the lunch-subordinate log file
            subordinate_log_file = "subordinate-%s.log" % (self.identifier)
            if not os.path.exists(self.subordinate_log_dir):
                try:
                    os.makedirs(self.subordinate_log_dir)
                except OSError, e:
                    raise RuntimeError("You need to be able to write in the current working directory in order to write log files. %s" % (e))
            self.subordinate_logger = logfile.LogFile(subordinate_log_file, self.subordinate_log_dir)
    
    def start(self):
        """
        Starts the lunch-subordinate process and its child if they are not running. 
        
        If the lunch-subordinate is already started, starts its child.
        """
        self.enabled = True
        #FIXME:2010-08-17:aalex:We won't reset the _has_shown_ssh_error state when starting, otherwise it shows the error many times.
        # self._has_shown_ssh_error = False
        self.gave_up = False
        if self.how_many_times_tried == 0:
            self._current_try_again_delay = self.try_again_delay
        self.how_many_times_tried += 1
        self._start_logger()
        # If the lunch-subordinate is already running, we only need to tell it to start the child
        if self.child_state == STATE_RUNNING:
            self.log("%s: Child is already running." % (self.identifier))
            return
        if self.subordinate_state == STATE_RUNNING and self.child_state == STATE_STOPPED:
            self._send_all_startup_commands()
            return
        elif self.child_state in [STATE_STOPPING, STATE_STARTING]:
            self.log("Cannot start child %s that is %s." % (self.identifier, self.child_state))
            return
        else:
            if self.subordinate_state in [STATE_STARTING, STATE_STOPPING]:
                self.log("Cannot start a lunch-subordinate %s that is %s." % (self.identifier, self.subordinate_state))
                return # XXX
            else: # lunch-subordinate is STOPPED
                # --------------- start the lunch-subordinate, and then its child
                self.number_of_lines_received_from_subordinate = 0
                self._received_ready = False
                if self.host is None:
                    # if self.user is not None:
                        # TODO: Set gid if user is not None...
                    is_remote = False # not using SSH
                    _command = ["lunch-subordinate", "--id", self.identifier]
                else:
                    self.log("We will use SSH since host is %s" % (self.host))
                    is_remote = True # using SSH
                    _command = ["ssh"]
                    if self.ssh_port is not None:
                        _command.extend(["-p", str(self.ssh_port)])
                    if self.user is not None:
                        _command.extend(["-l", self.user])
                    _command.extend([self.host])
                    _command.extend(["lunch-subordinate", "--id", self.identifier])
                    # I hope you put your SSH key on the remote host !
                    # FIXME: we should pop-up a terminal if keys are not set up.
                try:
                    _command[0] = procutils.which(_command[0])[0]
                except IndexError:
                    raise RuntimeError("Could not find path of executable %s." % (_command[0]))
                log.info("lunch-subordinate %s> $ %s" % (self.identifier, " ".join(_command)))
                self._process_protocol = SubordinateProcessProtocol(self)
                #try:
                proc_path = _command[0]
                args = _command
                environ = {}
                environ.update(os.environ) # passing the whole env (for SSH keys and more)
                self.set_subordinate_state(STATE_STARTING)
                self.log("Starting lunch-subordinate: %s" % (self.identifier))
                self._previous_launching_time = time.time()
                self._process_transport = reactor.spawnProcess(self._process_protocol, proc_path, args, environ, usePTY=True)
    
    def _format_env(self):
        """
        Format the environment variables to send them to the lunch-subordinate as a series of key-value pairs.
        """
        txt = ""
        for k, v in self.env.iteritems():
            txt += "%s=%s " % (k, v)
        return txt 
    
    def _on_connection_made(self):
        if self.subordinate_state == STATE_STARTING:
            self.set_subordinate_state(STATE_RUNNING)
        else:
            msg = "Connection made with lunch-subordinate %s, even if not expecting it." % (self.identifier)
            self.log(msg, logging.ERROR)

    def send_do(self):
        """
        Send to the lunch-subordinate the command line to launch its child.
        """
        self.send_message("do", self.command) 
    
    def send_ping(self):
        self.send_message("ping") # for fun

    def send_run(self):
        self.send_message("run")
        self.log("lunch-child %s> $ %s" % (self.identifier, self.command), logging.INFO)
        
    def send_env(self):
        self.send_message("env", self._format_env())
    
    def send_logdir(self):
        self.send_message("logdir", self.child_log_dir)

    def send_message(self, key, data=""):
        """
        Sends a command to the lunch-subordinate.
        @param key: string
        @param data: string
        """
        msg = "%s %s\n" % (key, data)
        if self.verbose:
            self.log("lunch-subordinate %s> Sending %s" % (self.identifier, msg.strip()))
        self._process_transport.write(msg)
    
    def __del__(self):
        #TODO: send "stop" and SIGKILL if the lunch-subordinate and the child processes are stil running.
        if self.subordinate_logger is not None:
            self.subordinate_logger.close()
        
    def _looks_like_ssh_error(self, line):
        """
        Checks the string received to see if it looks like a SSH error message.
        @return: An error message if there is a problem, or None otherwise.
        @rtype: C{str}
        """
        #XXX: if is returns a string, the main will give it up
        ret = None
        # log.debug("Checking if it looks like a SSH error: %s" % (line))
        if "password:" in line:
            ret = "The SSH server asks for a password. Make sure you use the right user name and that your public SSH key is installed on the remote host %s." % (self.host)
            #giving up
        elif "Enter passphrase for key" in line:
            ret = "The SSH client asks for a passphrase to unlock your local private SSH key for which you have the corresponding public key on host %s. You should avoid this to be asked by providing that passphrase a first time using SSH by hand." % (self.host)
            #giving up
        elif "Connection refused" in line:
            port = 22
            if self.ssh_port is not None:
                port = self.ssh_port
            ret = "The SSH server is not running on port %d of host %s or not available." % (port, self.host)
            #TODO: try to reconnect
        elif "No route to host" in line:
            ret = "We cannot find host %s." % (self.host)
            #TODO: try to reconnect
        elif "command not found" in line:
            ret = "The lunch-subordinate command is not installed on the host %s." % (self.host)
            #giving up
        elif "ssh_exchange_identification" in line: #FIXME: what is that?
            ret = "Some SSH problem occurred exchanging the identification on host %s. Is your host blacklisted?" % (self.host)
        elif "Could not resolve hostname" in line:
            ret = "Could not resolve hostname %s." % (self.host)
        if ret is not None:
            ret += "\nThe line received from SSH is :\n" + line
            ret += "\nThis error happend when trying to launch %s" % (self)
            log.error(line)
            log.error(ret)
        return ret

    def _received_message(self, line):
        """
        Received one line of text from the lunch-subordinate through its stdout.
        """
        #self.log("%8s: %s" % (self.identifier, line))
        # FIXME: right now, we check all the output from that guy
        if True: #self.number_of_lines_received_from_subordinate == 0:
            ssh_error = self._looks_like_ssh_error(line)
            if ssh_error is not None: # It's a str
                log.error("--------- SSH PROBLEM: " + ssh_error + " -----------")
                # FIXME: self.enabled = False
                if not self._has_shown_ssh_error:
                    self._has_shown_ssh_error = True
                    self.ssh_error_signal(self, ssh_error)
                return
                #TODO: handle this
        self.number_of_lines_received_from_subordinate += 1
        try:
            words = line.split(" ")
            key = words[0]
            mess = line[len(key) + 1:]
        except IndexError, e:
            #self.log("Index error parsing message from lunch-subordinate. %s" % (e), logging.ERROR)
            self.log('IndexError From lunch-subordinate %s: %s' % (self.identifier, line), logging.ERROR)
        else:
            # Dispatch the command to the appropriate method.  Note that all you
            # need to do to implement a new command is add another do_* method.
            if key in ["do", "env", "run", "logdir", "stop"]: # FIXME: receiving in stdin what we send to stdin lunch-subordinate !!!
                pass #warnings.warn("We receive from the lunch-subordinate's stdout what we send to its stdin !")
            else:
                try:
                    method = getattr(self, 'recv_' + key)
                except AttributeError, e:
                    self.log('AttributeError: Parsing a line from lunch-subordinate %s: %s' % (self.identifier, line), logging.ERROR)
                    #self.log(line)
                else:
                    method(mess)

    def recv_ok(self, mess):
        """
        Callback for the "ok" message from the lunch-subordinate.
        """
        pass

    def recv_not_found(self, mess):
        """
        Callback for the "not_found" message from the lunch-subordinate.
        
        That's when bash complains that it didn't find the command we are trying to run.
        """
        log.error("lunch-subordinate %s> Command not found: %s" % (self, self.command))
        if not self._has_shown_notfound_error:
            self._has_shown_notfound_error = True
            self.command_not_found_signal(self, self.command)

    def recv_child_pid(self, mess):
        """
        Callback for the "child_pid" message from the lunch-subordinate.
        
        The arg is the child's PID
        """
        self.log("lunch-subordinate %s> child_pid %s" % (self.identifier, mess))
        words = mess.split(" ")
        self.child_pid = int(words[0])
        self.log("%s: PID of child is %s" % (self.identifier, self.child_pid), logging.INFO)
        self.child_state_changed_signal(self, self.child_pid)

    def recv_msg(self, mess):
        """
        Callback for the "msg" message from the lunch-subordinate.
        """
        pass
    
    def recv_retval(self, mess):
        """
        Callback for the "retval" message from the lunch-subordinate.
        """
        self.log("lunch-subordinate %s> retval %s" % (self.identifier, mess))
        words = mess.split(" ")
        self.retval = int(words[0])
        self.log("%s: Return value of child is %s" % (self.identifier, self.retval), logging.INFO)
    
    def recv_log(self, mess):
        """
        Callback for the "log" message from the lunch-subordinate.
        """
        self.log("lunch-subordinate %s> log %s" % (self.identifier, mess))

    def recv_error(self, mess):
        """
        Callback for the "error" message from the lunch-subordinate.
        """
        self.log("lunch-subordinate %s> error %s" % (self.identifier, mess), logging.ERROR)
    
    def recv_pong(self, mess):
        """
        Callback for the "pong" message from the lunch-subordinate.
        """
        pass #self.log("pong from %s" % (self.identifier))

    def recv_bye(self, mess):
        """
        Callback for the "bye" message from the lunch-subordinate.
        """
        self.log("lunch-subordinate %s> %s" % (self.identifier, "BYE (subordinate quits)"), logging.ERROR)
    
    def get_state_info(self):
        """
        Returns a high-level comprehensive state for the user to see in the GUI.
        """
        #log.debug("gave up: %s" % (self.gave_up))
        if self.child_state == STATE_STOPPED:
            if self.how_many_times_run == 0:
                return INFO_TODO
            elif self.gave_up:
                return INFO_GAVEUP
            elif not self.respawn:
                return INFO_DONE
            elif not self.enabled:
                return STATE_STOPPED
            elif self.retval != 0:
                return INFO_FAILED
            else:
                return STATE_STOPPED # INFO_FAILED?
        else:
            return self.child_state
    
    def _give_up_if_we_should(self):
        """
        Check if we should give up and give up if so.
        """
        # double the time to wait before trying again.
        # self.wait_before_trying_again -- this one never changes
        # self._wait_before_trying_again_next_time -- this one is doubled each time.
        if self.give_up_after != 0 and self.how_many_times_tried > self.give_up_after:
            self.gave_up = True
            self.enabled = False
            log.info("Gave up restarting command %s" % (self.identifier))
        else:
            self._next_try_time = time.time() + self._current_try_again_delay
            log.info("%s: Will wait %f seconds before trying again." % (self.identifier, self._current_try_again_delay))
            self._current_try_again_delay *= 2
            self.how_many_times_tried += 1

    def recv_state(self, mess):
        """
        Callback for the "state" message from the child.
        Received child state.
        """
        words = mess.split(" ")
        previous_state = self.child_state
        new_state = words[0]
        #print("%s's child state: %s" % (self.identifier, new_state))
        self.log("lunch-child %s> Its state changed to %s" % (self.identifier, new_state))
        if new_state == STATE_STOPPED and self.enabled and self.respawn:
            child_running_time = float(words[1])
            if child_running_time < self.minimum_lifetime_to_respawn:
                self.log("lunch-child %s> Its running time of %s has been shorter than its minimum of %s." % (self.identifier, child_running_time, self.minimum_lifetime_to_respawn))
                self._give_up_if_we_should()
            #else:
            #    self._send_all_startup_commands()
        elif new_state == STATE_RUNNING:
            self.log("lunch-child %s> is running." % (self.identifier))
        self._set_child_state(new_state) # IMPORTANT !

    def recv_ready(self, mess):
        """
        Callback for the "ready" message from the lunch-subordinate.
        
        The lunch-subordinate sends that to the main when launched.
        It means it is ready to received commands.
        """
        self._received_ready = True
        if self.enabled:
            self._send_all_startup_commands()

    def _send_all_startup_commands(self):
        """
        Tells the lunch-subordinate to launch its child process.
        Sets up the environment and command so that the lunch-subordinate can launch the child.
        """
        self.send_do()
        self.send_logdir()
        self.send_env()
        #self.send_ping()
        self.send_run()

    def _set_child_state(self, new_state):
        """
        Called when it is time to change the state of the child process.
        """
        if new_state == STATE_STOPPED:
            self.child_pid = None
        if self.child_state != new_state:
            if new_state == STATE_RUNNING:
                self.how_many_times_run += 1
            self.child_state = new_state
        #    log.msg(" --------------- XXX Trigerring signal %s" % (self.child_state))
            self.child_state_changed_signal(self, self.child_state)

    def reset(self):
        """
        Do not give up anymore and reset the trials thing.
        """
        self.how_many_times_tried += 1
        self.gave_up = False
        self._next_try_time = 0
        self._current_try_again_delay = self.try_again_delay
    
    def stop(self):
        """
        Tells the lunch-subordinate to stop its child.
        """
        self.reset()
        self.enabled = False
        if self.child_state in [STATE_RUNNING, STATE_STARTING]:
            self.log('%s: stop' % (self.identifier), logging.INFO)
            self.send_stop()
        else:
            msg = "Cannot stop child %s that is %s." % (self.identifier, self.child_state)
            self.log(msg, logging.WARNING)

    def send_stop(self):
        self.send_message("stop")
    
    def quit_subordinate(self):
        """
        Stops the lunch-subordinate process by sending it SIGTERM. (15) 

        If called for a second time, sends it SIGKILL (9).

        One should first make sure the child process has quit before doing this, 
        otherwise they are likely to become zombie processes, with no parent. 

        @rtype: L{twisted.internet.defer.Deferred}
        """
        DELAY_BETWEEN_EACH_SIGNAL = self.delay_before_kill
        if self._quit_subordinate_deferred is not None:
            raise RuntimeError("Subordinate seems to be already quitting.")
        self._quit_subordinate_deferred = defer.Deferred()
        _sigkill_delayed_call = None
        
        def _on_ended(result):
            # cancels the call fo _cl_sigkill if the lunch-subordinate died.
            if _sigkill_delayed_call is not None:
                if _sigkill_delayed_call.active():
                    _sigkill_delayed_call.cancel()
            if not self._quit_subordinate_deferred.called:
                self._quit_subordinate_deferred.callback(None)
            return result
        
        def _cl_sigterm():
            # sends a sigterm
            # and later a sigkill
            self._process_transport.signalProcess(15) # signal.SIGTERM
            self.set_subordinate_state(STATE_STOPPING)
            self.log('Will stop lunch-subordinate %s.' % (self.identifier))
            _sigkill_delayed_call = reactor.callLater(DELAY_BETWEEN_EACH_SIGNAL, _cl_sigkill)
            # ---------------------------------------
        def _cl_sigkill():
            # sends sigkill if the lunch-subordinate is still running
            if self.subordinate_state == STATE_STOPPING:
                self.log("kill -9 Subordinate %s" % (self.identifier))
                self._process_transport.signalProcess(9) # signal.SIGKILL
            # ---------------------------------------
        
        self._quit_subordinate_deferred.addCallback(_on_ended) # XXX: could also be done with a signal/slot
        if self.subordinate_state == STATE_STOPPED:
            self.log("The lunch-subordinate process %s is already in \"%s\" state." % (self.identifier, self.subordinate_state), logging.WARNING)
            self._quit_subordinate_deferred.callback(None)
        else:
            if self.subordinate_state in [STATE_RUNNING, STATE_STARTING]:
                if self.child_state in [STATE_RUNNING, STATE_STARTING]:
                    self.stop() # self.send_stop()
                    reactor.callLater(DELAY_BETWEEN_EACH_SIGNAL, _cl_sigterm)
                elif self.child_state == STATE_STOPPED:
                    _cl_sigterm()
            elif self.subordinate_state == STATE_STOPPING:
                # second time this is called, force-quitting:
                self._process_transport.signalProcess(9) # signal.SIGKILL
                _cl_sigkill()
        return self._quit_subordinate_deferred

    def _on_process_ended(self, exit_code):
        """
        The lunch-subordinate died ! Its child is probably dead too. (otherwise, it's a zombie with no parent)
        """
        #TODO: add a signal slot for this event?
        #self.log("Exit code: " % (exit_code))
        former_subordinate_state = self.subordinate_state
        if former_subordinate_state == STATE_STARTING:
            self.log("Subordinate %s died during startup." % (self.identifier), logging.ERROR)
        elif former_subordinate_state == STATE_RUNNING:
            if exit_code == 0:
                self.log("Subordinate %s exited." % (self.identifier))
            else:
                self.log('Subordinate %s exited with error %s.' % (self.identifier, exit_code))
        elif former_subordinate_state == STATE_STOPPING:
            self.log('Subordinate exited as expected.')
        self.set_subordinate_state(STATE_STOPPED)
        self._process_transport.loseConnection()
        #if self.respawn and self.enabled: #No! The main will take care of that.
        #    self.log("Restarting the lunch-subordinate %s." % (self.identifier), logging.INFO)
        #    self.start()
        if self._quit_subordinate_deferred is not None:
            self._quit_subordinate_deferred.callback(None)
        
    def log(self, msg, level=logging.DEBUG):
        """
        Logs both to the lunch-subordinate's log file, and to the main app log. 
        """
        if self.subordinate_logger is not None:
            prefix = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            self.subordinate_logger.write("%s %s\n" % (prefix, msg))
            self.subordinate_logger.flush()
        log_number_to_name = {
            logging.DEBUG: 'debug',
            logging.INFO: 'info',
            logging.WARNING: 'warning',
            logging.ERROR: 'error',
            logging.CRITICAL: 'critical',
            }
        if level == logging.DEBUG:
            log.debug(msg)
        elif level == logging.INFO:
            log.info(msg)
        elif level == logging.WARNING:
            log.warning(msg)
        elif level == logging.ERROR:
            log.error(msg)
        elif level == logging.CRITICAL:
            log.critical(msg)

        #log.msg(msg, logLevel=level)

    def set_subordinate_state(self, new_state):
        """
        Trigger the subordinate_state_changed_signal when the state of the lunch-subordinate process changes.
        """
        msg = "Subordinate %s is %s." % (self.identifier, new_state)
        self.log(msg)
        if self.subordinate_state != new_state:
            self.subordinate_state = new_state
            self.subordinate_state_changed_signal(self.subordinate_state)

    def __str__(self):
        return "%s" % (self.identifier)
    

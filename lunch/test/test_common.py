"""
Tests for lunch Main
"""
from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import failure
from twisted.internet import reactor
from lunch import main
from lunch import commands
from lunch.states import *

LOG_LEVEL = "warning"
#LOG_LEVEL = "info"
#LOG_LEVEL = "debug"

main.start_stdout_logging(LOG_LEVEL)
from lunch import logger
log = logger.start(name="test")

#TODO: add the path to lunch-subordinate to $PATH

class Test_Main(unittest.TestCase):
    timeout = 4.0 # so that we don't wait in case of a problem
    def test_read_config(self):
        pass
    test_read_config.skip = "TODO."

    def test_add_remove_command(self):
        COMMAND_IDENTIFER = "test"
        COMMAND_LINE = "man man"
        _deferred = defer.Deferred()
        _main = main.Main()
        self.the_command = None
        
        def _later1():
            # checks the the command has been added
            # removes the command
            self.the_command = _main.get_command(COMMAND_IDENTIFER)
            log.info("Set self.the_command to %s" % (self.the_command))
            _main.remove_command(COMMAND_IDENTIFER)
            log.info("remove_command")
            reactor.callLater(0.1, _later2)
        
        def _later2():
            log.info("_later2")
            # checks the the command has been removed
            if len(_main.get_all_commands()) != 0:
                msg = "The command did not get removed"
                log.info(msg)
                _deferred.errback(failure.Failure(failure.DefaultException(msg)))
                log.info("failed")
            else:
                log.info("removing the looping call")
                if _main._looping_call.running:
                    d = _main._looping_call.deferred
                    _main._looping_call.stop() # FIXME
                    d.addCallback(_cb3)
                else:
                    _deferred.callback(None)
        
        def _cb3(result):
            # Called when the looping call has been stopped
            log.info("quit all subordinates")
            for command in _main.get_all_commands():
                command.quit_subordinate() #TODO: use the Deferred
            if self.the_command.subordinate_state == STATE_RUNNING:
                self.the_command.quit_subordinate() #TODO: use the Deferred
            reactor.callLater(0.1, _later4)
        
        def _later4():
            _deferred.callback(None)
        
        _main.add_command(commands.Command(COMMAND_LINE, identifier=COMMAND_IDENTIFER))
        log.info("added command $ %s" % (COMMAND_LINE))
        reactor.callLater(0.1, _later1)
        return _deferred
    

    test_add_remove_command.skip = "This test is still not working."
        
#class Test_Command(unittest.TestCase):
#    def test_configure(self):
#        pass
#    def test_start(self):
#        pass
#    def test_stop(self):
#        pass
#    test_configure.skip = "TODO."
#    test_start.skip = "TODO."
#    test_stop.skip = "TODO."





class Test_Main_Advanced(unittest.TestCase):
    timeout = 4.0 # so that we don't wait in case of a problem
    
    def setUp(self):
        self._main = main.Main()
        
    def test_add_commands_with_dependencies(self):
        COMMAND_A_IDENTIFIER = "test_A"
        COMMAND_B_IDENTIFIER = "test_B"
        COMMAND_C_IDENTIFIER = "test_C"
        COMMAND_LINE = "man man"
        DELAY = 0.1        
        self._deferred = defer.Deferred()

        self._main.add_command(commands.Command(COMMAND_LINE, identifier=COMMAND_A_IDENTIFIER))
        self._main.add_command(commands.Command(COMMAND_LINE, identifier=COMMAND_B_IDENTIFIER, depends=[COMMAND_A_IDENTIFIER]))
        self._main.add_command(commands.Command(COMMAND_LINE, identifier=COMMAND_C_IDENTIFIER, depends=[COMMAND_B_IDENTIFIER]))
        log.info("added commands")

        def _cl1():
            all = self._main.get_all_commands()
            self.failUnlessEqual(len(all), 3)
            _final_cleanup()

        def _final_cleanup():
            log.info("_final_cleanup")
            d = _tear_down()
            def _tear_down_cb(result):
                self._deferred.callback(None)
            d.addCallback(_tear_down_cb)
         
        def _tear_down():
            # return a deferred list
            log.info("_tear_down")
            # quit all subordinates
            return self._main.cleanup()

        #_final_cleanup()
        _cl1()
        #reactor.callLater(DELAY, _cl1)
        return self._deferred


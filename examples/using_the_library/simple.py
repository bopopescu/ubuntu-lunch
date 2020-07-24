#!/usr/bin/env python
"""
This example demonstrate how to use lunch main as a library.
"""
from twisted.internet import gtk2reactor
gtk2reactor.install() # has to be done before importing reactor
from twisted.internet import reactor
from twisted.internet import task
from lunch import commands
from lunch import main
from lunch import gui

if __name__ == "__main__":
    unique_main_id = "example"
    log_dir = main.DEFAULT_LOG_DIR
    pid_file = main.write_main_pid_file(identifier=unique_main_id, directory=log_dir)
    # XXX add_command here
    m = main.Main(log_dir=log_dir, pid_file=pid_file)
    m.add_command(commands.Command("xeyes", identifier="xeyes"))
    m.add_command(commands.Command("xlogo", identifier="xlogo"))
    m.add_command(commands.Command("xcalc", identifier="xcalc"))
    def _test():
        print("Adding one more!")
        m.add_command(commands.Command("xeyes"))
    looping_call = task.LoopingCall(_test)
    looping_call.start(1.0, False) 
    gui.start_gui(m)
    reactor.run()

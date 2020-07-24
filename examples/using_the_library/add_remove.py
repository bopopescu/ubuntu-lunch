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

has_them = False
counter = 0

if __name__ == "__main__":
    unique_main_id = "example"
    log_dir = main.DEFAULT_LOG_DIR
    
    main.start_logging()
    pid_file = main.write_main_pid_file(identifier=unique_main_id, directory=log_dir)
    m = main.Main(log_dir=log_dir, pid_file=pid_file, verbose=True)

    m.add_command(commands.Command("xeyes", identifier="xeyes"))
    m.add_command(commands.Command("xlogo", identifier="xlogo"))
    m.add_command(commands.Command("xcalc", identifier="xcalc"))
    m.add_command(commands.Command("xterm -hold -e /bin/bash -c \"echo %d\"" % (counter), identifier="xterm"))
    counter += 1
    has_them = True
    
    def _test():
        global has_them
        global counter
        if not has_them:
            print("Adding them again!")
            m.add_command(commands.Command("xeyes", identifier="xeyes"))
            m.add_command(commands.Command("xlogo", identifier="xlogo"))
            m.add_command(commands.Command("xcalc", identifier="xcalc"))
            m.add_command(commands.Command("xterm -hold -e /bin/bash -c \"echo %d\"" % (counter), identifier="xterm"))
            counter += 1
            has_them = True
        else:
            print("Removing them.")
            m.remove_command("xeyes")
            m.remove_command("xlogo")
            m.remove_command("xcalc")
            m.remove_command("xterm")
            has_them = False
            
    
    # a GUI!
    app = gui.start_gui(m)
    looping_call = task.LoopingCall(_test)
    looping_call.start(3.0, False)
    reactor.run()

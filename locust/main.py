from folders import find_locustfile, find_all_test_in_folder, load_locustfile
import locust
import runners

import gevent
import sys
import signal
import logging
import socket
from optparse import OptionParser

from log import setup_logging, console_logger
from stats import stats_printer, print_percentile_stats, print_error_report, print_stats
from inspectlocust import print_task_ratio, get_task_ratio_dict
from core import Locust, HttpLocust
from runners import MasterLocustRunner, SlaveLocustRunner, LocalLocustRunner
import events
from locust import web

_internals = [Locust, HttpLocust]
version = locust.version


def parse_options():
    """
    Handle command-line options with optparse.OptionParser.

    Return list of arguments, largely for use in `parse_arguments`.
    """

    # Initialize
    parser = OptionParser(usage="locust [options] [LocustClass [LocustClass2 ... ]]")

    parser.add_option(
        '-H', '--host',
        dest="host",
        default=None,
        help="Host to load test in the following format: http://10.21.32.33"
    )

    parser.add_option(
        '--web-host',
        dest="web_host",
        default="",
        help="Host to bind the web interface to. Defaults to '' (all interfaces)"
    )

    parser.add_option(
        '-P', '--port', '--web-port',
        type="int",
        dest="port",
        default=8089,
        help="Port on which to run web host"
    )

    parser.add_option(
        '-f', '--locustfile',
        dest='locustfile',
        default='locustfile',
        help="Python module file to import, e.g. '../other.py'. Default: locustfile"
    )

    parser.add_option(
        '-t', '--test-folder',
        dest='testfolder',
        default='locust-test',
        help="test folder to import, e.g. '../locust-test'. Default: locust-test"
    )

    # if locust should be run in distributed mode as master
    parser.add_option(
        '--master',
        action='store_true',
        dest='master',
        default=False,
        help="Set locust to run in distributed mode with this process as master"
    )

    # if locust should be run in distributed mode as slave
    parser.add_option(
        '--slave',
        action='store_true',
        dest='slave',
        default=False,
        help="Set locust to run in distributed mode with this process as slave"
    )

    # master host options
    parser.add_option(
        '--master-host',
        action='store',
        type='str',
        dest='master_host',
        default="127.0.0.1",
        help="Host or IP address of locust master for distributed load testing. Only used when running with --slave. Defaults to 127.0.0.1."
    )

    parser.add_option(
        '--master-port',
        action='store',
        type='int',
        dest='master_port',
        default=5557,
        help="The port to connect to that is used by the locust master for distributed load testing. Only used when running with --slave. Defaults to 5557. Note that slaves will also connect to the master node on this port + 1."
    )

    parser.add_option(
        '--master-bind-host',
        action='store',
        type='str',
        dest='master_bind_host',
        default="*",
        help="Interfaces (hostname, ip) that locust master should bind to. Only used when running with --master. Defaults to * (all available interfaces)."
    )

    parser.add_option(
        '--master-bind-port',
        action='store',
        type='int',
        dest='master_bind_port',
        default=5557,
        help="Port that locust master should bind to. Only used when running with --master. Defaults to 5557. Note that Locust will also use this port + 1, so by default the master node will bind to 5557 and 5558."
    )

    # if we should print stats in the console
    parser.add_option(
        '--no-web',
        action='store_true',
        dest='no_web',
        default=False,
        help="Disable the web interface, and instead start running the test immediately. Requires -c and -r to be specified."
    )

    # Number of clients
    parser.add_option(
        '-c', '--clients',
        action='store',
        type='int',
        dest='num_clients',
        default=1,
        help="Number of concurrent clients. Only used together with --no-web"
    )

    # Client hatch rate
    parser.add_option(
        '-r', '--hatch-rate',
        action='store',
        type='float',
        dest='hatch_rate',
        default=1,
        help="The rate per second in which clients are spawned. Only used together with --no-web"
    )

    # Number of requests
    parser.add_option(
        '-n', '--num-request',
        action='store',
        type='int',
        dest='num_requests',
        default=None,
        help="Number of requests to perform. Only used together with --no-web"
    )

    # log level
    parser.add_option(
        '--loglevel', '-L',
        action='store',
        type='str',
        dest='loglevel',
        default='INFO',
        help="Choose between DEBUG/INFO/WARNING/ERROR/CRITICAL. Default is INFO.",
    )

    # log file
    parser.add_option(
        '--logfile',
        action='store',
        type='str',
        dest='logfile',
        default=None,
        help="Path to log file. If not set, log will go to stdout/stderr",
    )

    # if we should print stats in the console
    parser.add_option(
        '--print-stats',
        action='store_true',
        dest='print_stats',
        default=False,
        help="Print stats in the console"
    )

    # only print summary stats
    parser.add_option(
        '--only-summary',
        action='store_true',
        dest='only_summary',
        default=False,
        help='Only print the summary stats'
    )

    # List locust commands found in loaded locust files/source files
    parser.add_option(
        '-l', '--list',
        action='store_true',
        dest='list_commands',
        default=False,
        help="Show list of possible locust classes and exit"
    )

    # Display ratio table of all tasks
    parser.add_option(
        '--show-task-ratio',
        action='store_true',
        dest='show_task_ratio',
        default=False,
        help="print table of the locust classes' task execution ratio"
    )
    # Display ratio table of all tasks in JSON format
    parser.add_option(
        '--show-task-ratio-json',
        action='store_true',
        dest='show_task_ratio_json',
        default=False,
        help="print json data of the locust classes' task execution ratio"
    )

    # Version number (optparse gives you --version but we have to do it
    # ourselves to get -V too. sigh)
    parser.add_option(
        '-V', '--version',
        action='store_true',
        dest='show_version',
        default=False,
        help="show program's version number and exit"
    )

    # Finalize
    # Return three-tuple of parser + the output from parse_args (opt obj, args)
    opts, args = parser.parse_args()
    return parser, opts, args


def main():
    parser, options, arguments = parse_options()

    # setup logging
    setup_logging(options.loglevel, options.logfile)
    logger = logging.getLogger(__name__)

    if options.show_version:
        print "Locust %s" % (version,)
        sys.exit(0)

    locust_file = find_locustfile(options.locustfile)
    tests_in_locust_folder = find_all_test_in_folder(options.testfolder)

    if not locust_file and not tests_in_locust_folder:
        logger.error("Could not find any locustfile! Ensure file ends in '.py' and see --help for available options.")
        sys.exit(1)

    if not tests_in_locust_folder:
        docstring, locusts = load_locustfile(locust_file)
    else:
        key = tests_in_locust_folder.keys()[0]
        docstring, locusts = tests_in_locust_folder[key]["locust"]

    if options.list_commands:
        console_logger.info("Available Locusts:")
        for name in locusts:
            console_logger.info("    " + name)
        sys.exit(0)

    if not locusts:
        logger.error("No Locust class found!")
        sys.exit(1)

    locust_classes_collection = {}
    for test_in_folder in tests_in_locust_folder:
        docstring, locusts = test_in_folder["locust"]
        if arguments:
            missing = set(arguments) - set(locusts.keys())
            if missing:
                logger.error("Unknown Locust(s): %s\n" % (", ".join(missing)))
                sys.exit(1)
            else:
                names = set(arguments) & set(locusts.keys())
                tests_in_locust_folder[test_in_folder.id]["locust"] = [locusts[n] for n in names]
        else:
            tests_in_locust_folder[test_in_folder.id]["locust"] = locusts.values()
    # make sure specified Locust exists

    if options.show_task_ratio:
        console_logger.info("\n Task ratio per locust class")
        console_logger.info("-" * 80)
        print_task_ratio(tests_in_locust_folder[0]["locust"])
        console_logger.info("\n Total task ratio")
        console_logger.info("-" * 80)
        print_task_ratio(locust_classes_collection[0]["locust"], total=True)
        sys.exit(0)
    if options.show_task_ratio_json:
        from json import dumps
        task_data = {
            "per_class": get_task_ratio_dict(locust_classes_collection[0]["locust"]),
            "total": get_task_ratio_dict(locust_classes_collection[0]["locust"], total=True)
        }
        console_logger.info(dumps(task_data))
        sys.exit(0)

    # if --master is set, make sure --no-web isn't set
    if options.master and options.no_web:
        logger.error(
            "Locust can not run distributed with the web interface disabled (do not use --no-web and --master together)")
        sys.exit(0)

    if not options.no_web and not options.slave:
        # spawn web greenlet
        logger.info("Starting web monitor at %s:%s" % (options.web_host or "*", options.port))
        main_greenlet = gevent.spawn(web.start, locust_classes_collection[0],  options)

    if not options.master and not options.slave:
        for test_in_folder in tests_in_locust_folder:
            locust_runner = LocalLocustRunner(test_in_folder["locust"], tests_in_locust_folder, options)
            runners.locust_runner = LocalLocustRunner(locust_runner, tests_in_locust_folder, options)

        # spawn client spawning/hatching greenlet
        if options.no_web:
            runners.locust_runner.start_hatching(wait=True)
            main_greenlet = runners.locust_runner.greenlet
    elif options.master:
        runners.locust_runner = MasterLocustRunner(locust_classes, tests_in_locust_folder, options)
    elif options.slave:
        try:
            runners.locust_runner = SlaveLocustRunner(locust_classes, tests_in_locust_folder, options)
            main_greenlet = runners.locust_runner.greenlet
        except socket.error, e:
            logger.error("Failed to connect to the Locust master: %s", e)
            sys.exit(-1)

    if not options.only_summary and (options.print_stats or (options.no_web and not options.slave)):
        # spawn stats printing greenlet
        gevent.spawn(stats_printer)

    def shutdown(code=0):
        """
        Shut down locust by firing quitting event, printing stats and exiting
        """
        logger.info("Shutting down (exit code %s), bye." % code)

        events.quitting.fire()
        print_stats(runners.locust_runner.request_stats)
        print_percentile_stats(runners.locust_runner.request_stats)

        print_error_report()
        sys.exit(code)

    # install SIGTERM handler
    def sig_term_handler():
        logger.info("Got SIGTERM signal")
        shutdown(0)

    gevent.signal(signal.SIGTERM, sig_term_handler)

    try:
        logger.info("Starting Locust %s" % version)
        main_greenlet.join()
        code = 0
        if len(runners.locust_runner.errors):
            code = 1
        shutdown(code=code)
    except KeyboardInterrupt as e:
        shutdown(0)


if __name__ == '__main__':
    main()

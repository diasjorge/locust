# encoding: utf-8

import csv
import json
import os.path
from time import time
from itertools import chain
from collections import defaultdict
from StringIO import StringIO

from gevent import wsgi
from flask import Flask, make_response, request, render_template

from locust import runners
from locust.cache import memoize
from locust.runners import MasterLocustRunner
from locust.stats import median_from_dict
from locust import version
import logging
from locust import runners_collector

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TIME = 2.0

app = Flask(__name__)
app.debug = True
app.root_path = os.path.dirname(os.path.abspath(__file__))


@app.route('/')
def index():
    is_distributed = isinstance(runners.locust_runner, MasterLocustRunner)
    if is_distributed:
        slave_count = runners.locust_runner.slave_count
    else:
        slave_count = 0

    return render_template("index.html",
                           state=runners.locust_runner.state,
                           is_distributed=is_distributed,
                           slave_count=slave_count,
                           user_count=runners.locust_runner.user_count,
                           version=version,
                           files=runners.locust_runner.files
                           )


@app.route('/test/<string:test_id>')
def bootstrap(test_id):
    test_runner = get_runner(test_id)
    test_runner.set_selected_locust(test_id)
    is_distributed = isinstance(test_runner, MasterLocustRunner)
    if is_distributed:
        slave_count = test_runner.slave_count
    else:
        slave_count = 0

    logger.info("test_id:%s test_runner:%s" % (test_id, test_runner))

    files = runners.locust_runner.files

    return render_template("test.html",
                           state=test_runner.state,
                           is_distributed=is_distributed,
                           slave_count=slave_count,
                           user_count=test_runner.user_count,
                           version=version,
                           files=files,
                           selected=test_id
                           )


# @app.route("/select-test/<string:test_id>", methods=["POST"])
# def select_test(test_id):
#    assert request.method == "POST"
#    runners.locust_runner.set_selected_locust(test_id)
#    response = make_response(json.dumps({'success': True, 'message': 'Test Changed'}))
#    response.headers["Content-type"] = "application/json"
#    return response


@app.route('/swarm', methods=["POST"])
def swarm_single():
    assert request.method == "POST"
    return swarm(runners.locust_runner, request)


@app.route('/test/<string:test_id>/swarm', methods=["POST"])
def swarm_test(test_id):
    assert request.method == "POST"
    return swarm(get_runner(test_id), request)


def swarm(runner, request):
    locust_count = int(request.form["locust_count"])
    hatch_rate = float(request.form["hatch_rate"])
    runner.start_hatching(locust_count, hatch_rate)
    response = make_response(json.dumps({'success': True, 'message': 'Swarming started'}))
    response.headers["Content-type"] = "application/json"
    return response


@app.route('/stop')
def stop_single():
    return stop(runners.locust_runner)


@app.route('/test/<string:test_id>/stop')
def stop_test(test_id):
    return stop(get_runner(test_id))


def stop(runner):
    runner.stop()
    response = make_response(json.dumps({'success': True, 'message': 'Test stopped'}))
    response.headers["Content-type"] = "application/json"
    return response


@app.route("/stats/reset")
def reset_stats_single():
    return reset_stats(runners.locust_runner)


@app.route("/test/<string:test_id>/stats/reset")
def reset_test_stats(test_id):
    return reset_stats(get_runner(test_id))


def reset_stats(runner):
    runner.stats.reset_all()
    return "ok"


@app.route("/stats/requests/csv")
def request_stats_csv_single():
    return request_stats_csv(runners.locust_runner)


@app.route("/test/<string:test_id>/stats/requests/csv")
def request_test_stats_csv(test_id):
    return request_stats_csv(get_runner(test_id))


def request_stats_csv(runner):
    rows = [
        ",".join([
            '"Method"',
            '"Name"',
            '"# requests"',
            '"# failures"',
            '"Median response time"',
            '"Average response time"',
            '"Min response time"',
            '"Max response time"',
            '"Average Content Size"',
            '"Requests/s"',
        ])
    ]

    for s in chain(_sort_stats(runner.request_stats),
                   [runner.stats.aggregated_stats("Total", full_request_history=True)]):
        rows.append('"%s","%s",%i,%i,%i,%i,%i,%i,%i,%.2f' % (
            s.method,
            s.name,
            s.num_requests,
            s.num_failures,
            s.median_response_time,
            s.avg_response_time,
            s.min_response_time or 0,
            s.max_response_time,
            s.avg_content_length,
            s.total_rps,
        ))

    response = make_response("\n".join(rows))
    file_name = "requests_{0}.csv".format(time())
    disposition = "attachment;filename={0}".format(file_name)
    response.headers["Content-type"] = "text/csv"
    response.headers["Content-disposition"] = disposition
    return response


@app.route("/stats/distribution/csv")
def distribution_stats_csv_single():
    return distribution_stats_csv(runners.locust_runner)


@app.route("/test/<string:test_id>/stats/distribution/csv")
def distribution_test_stats_csv(test_id):
    return distribution_stats_csv(get_runner(test_id))


def distribution_stats_csv(runner):
    rows = [",".join((
        '"Name"',
        '"# requests"',
        '"50%"',
        '"66%"',
        '"75%"',
        '"80%"',
        '"90%"',
        '"95%"',
        '"98%"',
        '"99%"',
        '"100%"',
    ))]
    for s in chain(_sort_stats(runner.request_stats),
                   [runner.stats.aggregated_stats("Total", full_request_history=True)]):
        if s.num_requests:
            rows.append(s.percentile(tpl='"%s",%i,%i,%i,%i,%i,%i,%i,%i,%i,%i'))
        else:
            rows.append('"%s",0,"N/A","N/A","N/A","N/A","N/A","N/A","N/A","N/A","N/A"' % s.name)

    response = make_response("\n".join(rows))
    file_name = "distribution_{0}.csv".format(time())
    disposition = "attachment;filename={0}".format(file_name)
    response.headers["Content-type"] = "text/csv"
    response.headers["Content-disposition"] = disposition
    return response


# @app.route('/stats/requests')
# @memoize(timeout=DEFAULT_CACHE_TIME, dynamic_timeout=True)
# def request_stats_single():
#    return request_stats(runners.locust_runner)


@app.route('/test/<string:test_id>/stats/requests')
@memoize(timeout=DEFAULT_CACHE_TIME, dynamic_timeout=True)
def request_test_stats(test_id):
    return request_stats(get_runner(test_id))


def request_stats(runner):
    stats = []
    for s in chain(_sort_stats(runner.request_stats),
                   [runner.stats.aggregated_stats("Total")]):
        stats.append({
            "method": s.method,
            "name": s.name,
            "num_requests": s.num_requests,
            "num_failures": s.num_failures,
            "avg_response_time": s.avg_response_time,
            "min_response_time": s.min_response_time or 0,
            "max_response_time": s.max_response_time,
            "current_rps": s.current_rps,
            "median_response_time": s.median_response_time,
            "avg_content_length": s.avg_content_length,
        })

    report = {"stats": stats, "errors": [e.to_dict() for e in runner.errors.itervalues()]}
    if stats:
        report["total_rps"] = stats[len(stats) - 1]["current_rps"]
        report["fail_ratio"] = runner.stats.aggregated_stats("Total").fail_ratio

        # since generating a total response times dict with all response times from all
        # urls is slow, we make a new total response time dict which will consist of one
        # entry per url with the median response time as key and the number of requests as
        # value
        response_times = defaultdict(int)  # used for calculating total median
        for i in xrange(len(stats) - 1):
            response_times[stats[i]["median_response_time"]] += stats[i]["num_requests"]

        # calculate total median
        stats[len(stats) - 1]["median_response_time"] = median_from_dict(stats[len(stats) - 1]["num_requests"],
                                                                         response_times)

    is_distributed = isinstance(runner, MasterLocustRunner)
    if is_distributed:
        report["slave_count"] = runner.slave_count

    report["state"] = runner.state
    report["user_count"] = runner.user_count
    return json.dumps(report)


@app.route("/exceptions")
def exceptions_single():
    return exceptions(runners.locust_runner)


@app.route("/test/<string:test_id>/exceptions")
def exceptions_test(test_id):
    return exceptions(get_runner(test_id))


def exceptions(runner):
    response = make_response(json.dumps({'exceptions': [
        {"count": row["count"], "msg": row["msg"], "traceback": row["traceback"], "nodes": ", ".join(row["nodes"])} for
        row in runner.exceptions.itervalues()]}))
    response.headers["Content-type"] = "application/json"
    return response


@app.route("/exceptions/csv")
def exceptions_csv_single():
    return exceptions_csv(runners.locust_runner)


@app.route("/test/<string:test_id>/exceptions/csv")
def exceptions_test_csv(test_id):
    return exceptions_csv(get_runner(test_id))


def exceptions_csv(runner):
    data = StringIO()
    writer = csv.writer(data)
    writer.writerow(["Count", "Message", "Traceback", "Nodes"])
    for exc in runner.exceptions.itervalues():
        nodes = ", ".join(exc["nodes"])
        writer.writerow([exc["count"], exc["msg"], exc["traceback"], nodes])

    data.seek(0)
    response = make_response(data.read())
    file_name = "exceptions_{0}.csv".format(time())
    disposition = "attachment;filename={0}".format(file_name)
    response.headers["Content-type"] = "text/csv"
    response.headers["Content-disposition"] = disposition
    return response


def start(locust, options):
    wsgi.WSGIServer((options.web_host, options.port), app, log=None).serve_forever()


def _sort_stats(stats):
    return [stats[key] for key in sorted(stats.iterkeys())]


def get_runner(test_id):
    return runners.locust_runner
  #  return runners.locust_runner.runner_collector.runners[test_id]['runner']


def get_runners():
    return runners.locust_runner
    #return runners.locust_runner.runner_collector.runners

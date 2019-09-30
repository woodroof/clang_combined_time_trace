#!/usr/bin/env python3
"""Script for combining multiply clang traces.
It loads all json files from specified directory and generate file with total
build time information and list of most time-consuming header files and
function and class instantiations."""

from contextlib import contextmanager
from operator import attrgetter
from pathlib import Path
import os

EXPECTED_PID = 1
EXPECTED_TID_FOR_NON_UNIQUE_EVENTS = 0

EXPECTED_EVENT_TYPES = frozenset(['M', 'X'])
IGNORED_EVENT_TYPES = frozenset(['M'])

IGNORED_EVENT_NAMES = frozenset([
    'Total ParseClass',
    'Total ParseTemplate',
    'Total PerformPendingInstantiations',
    'Total OptModule',
    'Total OptFunction',
    'Total RunPass',
    'Total RunLoopPass',
    'Total CodeGen Function',
    'ExecuteCompiler',
    'Frontend',
    'Backend',
    'OptModule',
    'OptFunction',
    'RunPass',
    'RunLoopPass',
    'ParseClass',
    'ParseTemplate',
    'PerformPendingInstantiations',
    'CodeGen Function',
])
PROCESSED_UNIQUE_EVENT_NAMES = frozenset([
    'Total ExecuteCompiler',
    'Total Frontend',
    'Total Source',
    'Total InstantiateFunction',
    'Total InstantiateClass',
    'Total Backend',
])
PROCESSED_NON_UNIQUE_EVENT_NAMES = frozenset([
    'Source',
    'InstantiateFunction',
    'InstantiateClass',
])


class ParentHeaderInfo:
    def __init__(self, file_name, duration):
        self.count = 1
        self.file_name = file_name
        self.duration = duration


class ParentSourcesInfo:
    def __init__(self):
        self.count = 0
        self.duration = 0


class Entity:
    def __init__(self, name, event_name, duration):
        self.name = name
        self.event_name = event_name
        self.duration = duration
        self.parent_sources_info = ParentSourcesInfo()
        self.parent_header_files = {}

    def combine(self, event):
        assert self.name == event.name, \
            """Trying to combine events for different entities:
            {} and {}""".format(self.name, event.name)
        assert self.event_name == event.event_name, \
            'Inconsistent event names for entity {}'.format(self.name)

        self.duration += event.duration
        self.parent_sources_info.count += event.parent_sources_info.count
        self.parent_sources_info.duration += event.parent_sources_info.duration

        for header_name, header_info in event.parent_header_files.items():
            if header_name in self.parent_header_files:
                self_header_info = self.parent_header_files[header_name]
                self_header_info.count += header_info.count
                self_header_info.duration += header_info.duration
            else:
                self.parent_header_files[header_name] = header_info

    def type(self):
        if self.event_name == 'Source':
            return 'header file processing'
        if self.event_name == 'InstantiateFunction':
            return 'function instantiation'
        assert self.event_name == 'InstantiateClass'
        return 'class instantiation'


class Statistics:
    def __init__(self):
        self.total = {}
        for unique_event_name in PROCESSED_UNIQUE_EVENT_NAMES:
            self.total[unique_event_name] = 0
        self.entities = {}


class TraceStatistics(Statistics):
    class TimeInfo:
        def __init__(self, entity_name, timestamp, duration):
            self.entity_name = entity_name
            self.timestamp = timestamp
            self.duration = duration

    def __init__(self):
        Statistics.__init__(self)
        self._time_infos = []
        self._finished = False

    # Format:
    # {"traceEvents": [{...},{...},{...}]}
    #   ph - event type
    #   ts - event timestamp
    #   name - event name
    #   pid - process id
    #   tid - thread id
    #   dur - microseconds
    #   args/detail
    #     file path for Source
    #     function name for InstantiateFunction
    #     class name for InstantiateClass
    def load_event(self, event):
        event_type = event['ph']
        assert event_type in EXPECTED_EVENT_TYPES, \
            'Unexpected event type {}'.format(event_type)
        assert event['pid'] == EXPECTED_PID, 'Expected only one process'

        if (event_type in IGNORED_EVENT_TYPES
                or event['name'] in IGNORED_EVENT_NAMES):
            return

        event_name = event['name']
        duration = event['dur']
        assert type(duration) is int, 'Attribute "dur" expected to be int'

        if event_name in PROCESSED_UNIQUE_EVENT_NAMES:
            self._set_total(event_name, duration)
        else:
            assert event['tid'] == EXPECTED_TID_FOR_NON_UNIQUE_EVENTS, \
                'Expected only one thread for non-unique events'
            assert event_name in PROCESSED_NON_UNIQUE_EVENT_NAMES, \
                'Unexpected event name {}'.format(event_name)

            entity_name = event['args']['detail']
            assert type(entity_name) is str, \
                'Attribute "args/detail" expected to be str'

            self._add_event(Entity(entity_name, event_name, duration))

            if event_name == 'Source':
                timestamp = event['ts']
                assert type(timestamp) is int, \
                    'Attribute "ts" expected to be int'

                self._time_infos.append(
                    self.TimeInfo(entity_name, timestamp, duration))

    def finish(self):
        assert not self._finished, \
            'TraceStatistics::finish should be called only once'
        self._finished = True

        waiting_entity_infos = []

        for time_info in self._time_infos:
            while waiting_entity_infos and time_info.timestamp <= waiting_entity_infos[-1].timestamp:
                waiting_entity_info = waiting_entity_infos.pop()
                assert waiting_entity_info.timestamp + waiting_entity_info.duration <= time_info.timestamp + time_info.duration, \
                    'Parent event appeared before child'

                entity = self.entities[waiting_entity_info.entity_name]
                parent_files = entity.parent_header_files
                if time_info.entity_name in parent_files:
                    parent_files[time_info.entity_name].count += 1
                    parent_files[time_info.entity_name].duration += \
                        waiting_entity_info.duration
                else:
                    parent_files[time_info.entity_name] = ParentHeaderInfo(time_info.entity_name, waiting_entity_info.duration)

            waiting_entity_infos.append(time_info)

        for waiting_entity_info in waiting_entity_infos:
            parent_info = self.entities[waiting_entity_info.entity_name].parent_sources_info
            parent_info.count += 1
            parent_info.duration += waiting_entity_info.duration

    def _add_event(self, entity):
        if entity.name in self.entities:
            existed_entity = self.entities[entity.name]
            assert existed_entity.event_name == entity.event_name, \
                'Inconsistent event names for entity {}'.format(entity.name)

            existed_entity.combine(entity)
        else:
            self.entities[entity.name] = entity

    def _set_total(self, name, duration):
        assert self.total[name] == 0, 'Non-unique event {}'.format(name)

        self.total[name] = duration


class TotalStatistics(Statistics):
    def __init__(self):
        Statistics.__init__(self)
        self.file_count = 0
        self.total_trace_file_size = 0

    def process_file_stats(self, file_size, file_statistics):
        self.file_count += 1
        self.total_trace_file_size += file_size

        file_statistics.finish()

        for name, time in file_statistics.total.items():
            self.total[name] += time

        for entity_name, entity in file_statistics.entities.items():
            if entity_name in self.entities:
                self.entities[entity_name].combine(entity)
            else:
                self.entities[entity_name] = entity


class ProgressBar:
    def __init__(self, total):
        self.total = total
        self.current = 0
        self.prev_len = 0

    def next(self, string):
        self._clear()

        self.current += 1
        assert self.current <= self.total, 'Incorrect total task count'

        current_string = '[{}/{}] {}'.format(self.current, self.total, string)
        self.prev_len = len(current_string)
        print(current_string, end='', flush=True)

    def _clear(self):
        import sys

        if self.current == 0:
            return

        if sys.__stdout__.isatty():
            print('\r', end='')
            print(' ' * self.prev_len, end='')
            print('\r', end='')
        else:
            print('')


@contextmanager
def create_progress_bar(total):
    progress_bar = ProgressBar(total)
    yield progress_bar
    print('', flush=True)


def find_traces(dir_path):
    trace_list = []
    for subpath in dir_path.iterdir():
        if subpath.is_dir():
            trace_list.extend(find_traces(subpath))
        if subpath.is_file() and os.path.splitext(subpath.parts[-1])[1] == '.json':
            trace_list.append(subpath)
    return trace_list


def load_trace(trace):
    trace_stats = TraceStatistics()
    for trace_event in trace['traceEvents']:
        trace_stats.load_event(trace_event)
    return trace_stats


def load_traces(progress_bar, trace_list):
    import json

    stats = TotalStatistics()

    for trace in trace_list:
        progress_bar.next(trace.parts[-1])

        with open(trace) as json_file:
            trace_stats = load_trace(json.load(json_file))

        stats.process_file_stats(os.path.getsize(trace), trace_stats)

    return stats


def format_time_seconds(time_mcs):
    return '{:.2f}s'.format(time_mcs / 1000000)


def format_part_time_seconds(time_mcs, total_mcs):
    return '{} ({:.2%})'.format(
        format_time_seconds(time_mcs), time_mcs / total_mcs)


def generate_txt(stats, file_path, list_size, parent_header_list_size):
    with open(file_path, 'w') as txt_file:
        entities_info = sorted(stats.entities.values(), reverse=True, key=attrgetter('duration'))[:list_size]

        txt_file.write('Processed file count: {}\n'.format(stats.file_count))
        txt_file.write('Total trace file size: {}\n'.format(stats.total_trace_file_size))
        txt_file.write('\n')

        total = stats.total
        total_time = total['Total ExecuteCompiler']

        txt_file.write('Total compiler time: {}\n'.format(format_time_seconds(total_time)))
        txt_file.write('  Total frontend time: {}\n'.format(format_part_time_seconds(total['Total Frontend'], total_time)))
        txt_file.write('    Total include files processing time: {}\n'.format(format_part_time_seconds(total['Total Source'], total_time)))
        txt_file.write('    Total instantiation time: {}\n'.format(format_part_time_seconds(total['Total InstantiateFunction'] + total['Total InstantiateClass'], total_time)))
        txt_file.write('  Total backend time: {}\n'.format(format_part_time_seconds(total['Total Backend'], total_time)))
        txt_file.write('\n')

        txt_file.write('Top {} entities:\n\n'.format(list_size))

        for entity in entities_info:
            parent_sources_info = entity.parent_sources_info
            txt_file.write('{}: {}: {}\n'.format(format_part_time_seconds(entity.duration, total_time), entity.type(), entity.name))
            txt_file.write('  Included from {} files:\n'.format(len(entity.parent_header_files) + parent_sources_info.count))
            sorted_parent_headers = sorted(entity.parent_header_files.values(), reverse=True, key=attrgetter('duration'))
            for parent_header in sorted_parent_headers[:parent_header_list_size]:
                txt_file.write('    {}: {} {}\n'.format(format_part_time_seconds(parent_header.duration, total_time), parent_header.count, parent_header.file_name))
            if len(sorted_parent_headers) > parent_header_list_size:
                txt_file.write('    ...\n')
            if parent_sources_info.count:
                txt_file.write('    {}: {} source files\n'.format(format_part_time_seconds(parent_sources_info.duration, total_time), parent_sources_info.count))


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-s', '--size', default=500, type=int, help='max size of list with most time-consuming entities')
    parser.add_argument('--parent_header_list_size', default=5, type=int, help='max size of parent headers list')
    parser.add_argument('source', help='path to directory with clang traces (see -ftime-trace)')
    parser.add_argument('destination', help='path to result file')

    args = parser.parse_args()

    trace_list = find_traces(Path(args.source))

    with create_progress_bar(len(trace_list) + 1) as progress_bar:
        stats = load_traces(progress_bar, trace_list)
        progress_bar.next('Generating output file')
        generate_txt(stats, Path(args.destination), args.size, args.parent_header_list_size)

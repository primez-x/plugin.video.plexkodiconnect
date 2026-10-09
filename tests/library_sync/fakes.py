"""Fakes for library sync DB-lock batching tests."""
from types import SimpleNamespace


class LibrarySyncMixin(object):
    pass


class KillableThread(object):
    def __init__(self, *args, **kwargs):
        self.sleeps = []

    def should_cancel(self):
        return False

    def sleep(self, timeout):
        self.sleeps.append(timeout)


class FakeSection(object):
    """Records which items were written within which DB context."""
    def __init__(self, name='Movies', events=None):
        self.name = name
        self.section_id = 1
        self.plex_type = 'movie'
        self.count = 0
        self.sync_successful = True
        self.events = [] if events is None else events
        self.locked = False

    def __bool__(self):
        return self.name is not None

    def context(self, current_time):
        section = self

        class Context(object):
            def __init__(self):
                self.plexdb = SimpleNamespace(
                    update_last_sync=lambda *args: None)

            def __enter__(self):
                section.events.append('enter')
                section.locked = True
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                section.locked = False
                section.events.append('exit')
                return False

            def add_update(self, xml, **kwargs):
                section.events.append(xml.get('title'))

            def update_userdata(self, xml, plex_type):
                section.events.append(xml.get('title'))
                return True

            def commit(self):
                section.events.append('commit')

        return Context()


def empty_section():
    return FakeSection(name=None)

"""Shared safety budgets, independent of application settings and database imports."""


class WorkLimit(Exception):
    def __init__(self, message: str, *, busy: bool = False):
        super().__init__(message)
        self.status_code = 503 if busy else 413


MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 512
MAX_EXPANDED_BYTES = 96 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_SHEETS = 40
MAX_ROWS = 10000
MAX_COLUMNS = 512
MAX_CELLS = 500000
MAX_PAGES = 200
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_DRAFTS = 1000
MAX_ITEMS = 20000
MAX_DEMANDS = 1000
MAX_OPTIONS = 200
MAX_PACKS = 250000
MAX_BOXES = 2000
MAX_PACKING_CHECKS = 2000000

class StorageError(Exception):
    """Safe, non-provider-specific error without credential/response reflection."""


class UploadTooLarge(ValueError):
    pass

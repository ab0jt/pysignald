import attr


@attr.s
class Attachment:
    content_type = attr.ib(type=str)
    id = attr.ib(type=str)
    size = attr.ib(type=int)
    stored_filename = attr.ib(type=str)


@attr.s
class Message:
    username = attr.ib(type=str)
    source = attr.ib(type=str)
    text = attr.ib(type=str)
    source_device = attr.ib(type=int, default=0)
    timestamp = attr.ib(type=int, default=None)
    timestamp_iso = attr.ib(type=str, default=None)
    attachments = attr.ib(type=list, default=[])
    group_info = attr.ib(type=dict, default={})
    group_list = attr.ib(type=list, default=[])

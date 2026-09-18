"""附件收集与投影服务。"""

from services.attachments.collector import AttachmentCollector, AttachmentFileReader
from services.attachments.context_preparer import AttachmentContextPreparer
from services.attachments.projector import AttachmentProjector
from services.attachments.types import AttachmentMessage, AttachmentScope

__all__ = [
    "AttachmentCollector",
    "AttachmentContextPreparer",
    "AttachmentFileReader",
    "AttachmentMessage",
    "AttachmentProjector",
    "AttachmentScope",
]

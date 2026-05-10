import io
import logging

from django.conf import settings
from minio import Minio

logger = logging.getLogger("dbt_platform.knowledge_base")


def get_minio_client() -> Minio:
    endpoint = settings.MINIO_ENDPOINT
    secure = settings.MINIO_SECURE
    return Minio(
        endpoint,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=secure,
    )


def upload_document(file_data, object_name: str, content_type: str = "application/octet-stream") -> str:
    """Upload a document to MinIO. Accepts bytes or file-like objects."""
    client = get_minio_client()
    bucket = settings.MINIO_BUCKET

    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("Created MinIO bucket: %s", bucket)

    if isinstance(file_data, bytes):
        file_size = len(file_data)
        file_obj = io.BytesIO(file_data)
    else:
        file_data.seek(0, io.SEEK_END)
        file_size = file_data.tell()
        file_data.seek(0)
        file_obj = file_data

    client.put_object(
        bucket,
        object_name,
        file_obj,
        file_size,
        content_type=content_type,
    )
    logger.info("Uploaded document to MinIO: %s (%d bytes)", object_name, file_size)
    return object_name


def download_document(object_name: str) -> bytes:
    """Download a document from MinIO."""
    client = get_minio_client()
    bucket = settings.MINIO_BUCKET
    response = client.get_object(bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def delete_document(object_name: str) -> None:
    """Delete a document from MinIO."""
    client = get_minio_client()
    bucket = settings.MINIO_BUCKET
    client.remove_object(bucket, object_name)
    logger.info("Deleted document from MinIO: %s", object_name)

from minio import Minio

client = Minio(
    "192.168.100.5:9000",      # MinIO 地址，不要带 http://
    access_key="minioadmin",
    secret_key="minioadmin123",
    secure=False,               # 没用 HTTPS 就设 False
)

BUCKET = "knowledge"

# 桶不存在就建。放启动时执行一次即可
if not client.bucket_exists(BUCKET):
    client.make_bucket(BUCKET)

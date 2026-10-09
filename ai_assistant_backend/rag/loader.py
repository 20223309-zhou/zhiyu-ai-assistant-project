"""按文件类型加载文档并切分成文本段。

供知识库入库流程调用：从 MinIO 下载的文件先落成临时文件，
再交给 load_and_split，返回可直接写入向量库的文本段。
"""

from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    UnstructuredMarkdownLoader,
    UnstructuredWordDocumentLoader,
)
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# content_type -> (加载器类, 额外参数)
_LOADERS = {
    "application/pdf": (PyPDFLoader, {"mode": "single", "extraction_mode": "plain"}),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        UnstructuredWordDocumentLoader, {"mode": "elements"},
    ),
    "text/markdown": (UnstructuredMarkdownLoader, {"mode": "single", "strategy": "fast"}),
    "text/plain": (TextLoader, {"encoding": "utf-8"}),
    "text/csv": (TextLoader, {"encoding": "utf-8"}),
}

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=450,       # 每块大小
    chunk_overlap=50,      # 每块重叠大小
    length_function=len,
)

def load_only(file_path: str, content_type: str) -> list[Document]:
    """按 content_type 选择加载器并加载，**不做切分**。

    给「不需要分段」的场景用。最典型的是审核前预览正文：
    切分是为了写入向量库，分段之间带 chunk_overlap，把分段拼回去
    会在每个接缝处重复几十字，读起来像文档自己重复了一段。
    分段是存储细节，不是内容本身 —— 要判断内容合规，应该看原始文本。

    不支持的类型抛 ValueError，由调用方决定如何处理。
    """
    spec = _LOADERS.get(content_type)
    if spec is None:
        raise ValueError(f"不支持解析的文件类型: {content_type}")

    loader_cls, kwargs = spec
    return loader_cls(file_path=file_path, **kwargs).load()


def load_and_split(file_path: str, content_type: str) -> list[Document]:
    """加载后切分成文本段，返回可直接写入向量库的分段。

    不支持的类型抛 ValueError，由调用方决定如何处理。
    """
    documents = load_only(file_path, content_type)

    chunks = _splitter.split_documents(documents)
    for chunk in chunks:
        chunk.metadata["len"] = len(chunk.page_content)
    return chunks

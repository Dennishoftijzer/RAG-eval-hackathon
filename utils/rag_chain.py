# Importing necessary libraries and modules
import json
import os
from operator import itemgetter
from pathlib import Path
from typing import List

from dotenv import load_dotenv  # For loading environment variables from .env file
from langchain.retrievers.multi_query import MultiQueryRetriever
from langchain.schema import Document  # LangChain's Document schema
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma  # Chroma for vector storage
from langchain_community.document_loaders import PyMuPDFLoader, DirectoryLoader
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder  # For creating chat prompt templates
from langchain_core.pydantic_v1 import BaseModel  # Base model for Pydantic
from langchain_core.runnables import RunnableParallel, RunnablePassthrough, RunnableLambda  # For creating runnable pipelines
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings  # Azure OpenAI interfaces for LangChain
from langchain.chains import create_retrieval_chain, create_history_aware_retriever
from langchain.chains.combine_documents import create_stuff_documents_chain

from langchain_together import ChatTogether

from langchain_core.output_parsers import StrOutputParser

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
AZURE_OPENAI_API_VERSION=os.getenv("AZURE_OPENAI_API_VERSION")
AZURE_EMBEDDING_MODEL_NAME = os.getenv("AZURE_EMBEDDING_MODEL_NAME")
AZURE_CHAT_MODEL_NAME = os.getenv("AZURE_CHAT_MODEL_NAME")

def load_azure_embedding_model() -> AzureOpenAIEmbeddings:
    """create embedding model based on input

    Args:
        deployment_name (str): the string of the model or deployment name
        api_version (str): api_version

    Returns:
        embedding_model (AzureOpenAIEmbeddings): The embedding model
    """
    embed = AzureOpenAIEmbeddings(
        openai_api_version=AZURE_OPENAI_API_VERSION,
        azure_deployment=AZURE_EMBEDDING_MODEL_NAME
    )
    # The number of pieces of Documents that get send to the Azure OpenAI Embedding model to be embedded.
    # Higher number will take longer to process, but requires less requests to the Azure service and
    # limits the 429 response code: too many requests
    embed.chunk_size = 1024
    return embed

def load_azure_chat_model() -> AzureChatOpenAI:
    chat_model = AzureChatOpenAI(
        openai_api_version=AZURE_OPENAI_API_VERSION,
        azure_deployment=AZURE_CHAT_MODEL_NAME,
        temperature=0.0,
        streaming=True,
    )
    return chat_model

def load_together_chat_model(model: str) -> ChatTogether: 
    chat_model = ChatTogether(
        model=model,
        temperature=0,
        max_tokens=None,
        timeout=None,
        max_retries=2,
    )
    return chat_model

def load_pdf_docs(dir: str): 
    # Load PDF files
    loader = DirectoryLoader(dir, glob="*.pdf", loader_cls=PyMuPDFLoader)
    documents = loader.load()

    # Replace source with arxiv id and paper title
    for doc in documents:
        doc.metadata['source'] = '.'.join(os.path.basename(doc.metadata['source']).split('.')[:2])

    return documents

def load_persistent_retriever(embedding_model, data_root, documents, chunk_size, chunk_overlap, collection_name):
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    split_docs = text_splitter.split_documents(documents)

    # Define the directory to persist the vector database
    vectorstore_persist_directory = os.path.join(data_root, "vectorstore")

    # Check if the vectorstore directory exists; if so, load it, otherwise create a new one from documents
    if os.path.exists(vectorstore_persist_directory):
        vectorstore = Chroma(collection_name=collection_name, embedding_function=embedding_model,
                            persist_directory=vectorstore_persist_directory)
    else:
        vectorstore = Chroma.from_documents(
            documents=split_docs,
            collection_name=collection_name,
            embedding=embedding_model,
            persist_directory=vectorstore_persist_directory
        )

    retriever = vectorstore.as_retriever(fetch_k=3)

    return retriever

def load_RAG_chain(system_prompt, chat_model, retriever):
    # Define a prompt template for the AI to follow when generating responses
    prompt = ChatPromptTemplate.from_messages(
    [
        ("system", system_prompt),
        ("human", "{question}"),
    ]
    )

    def format_docs(docs):
        return "\n\n".join(doc.page_content for doc in docs)
    
    qa_chain = (
    {
        "context": retriever | format_docs,
        "question": RunnablePassthrough(),
    }
    | prompt
    | chat_model
    | StrOutputParser()
    )

    return qa_chain
# Permission-Aware RAG

## Project Overview

A secure enterprise RAG system that aims to generate answers using only information each user is authorized to access, even when data is distributed across services with different access policies.

## Challenges

- **Latency:** Permission checks across services can slow retrieval.
- **IAM failures:** Authorization service outages can interrupt queries.
- **Stale permissions:** Cached access rights may persist after revocation.
- **Index rigidity:** File or permission changes should not require vector re-indexing.
- **Empty context:** The system should avoid unsupported answers when no authorized content is available.
- **Privacy:** Sensitive queries should be processed locally when needed.

## Intended Direction

We aim to enforce least-privilege access across AWS, GCP, and Keycloak; reduce delays through parallel permission checks; separate metadata from vector data; handle IAM failures and permission changes; and support local inference with Ollama and potential LoRA adaptation. Responses should rely only on authorized information.

## Research Reference

Jooyoung Jeong and Sang-Goo Lee, “Permission-Aware RAG: Identity and Access Management (IAM)-Based Access Filtering in Multi-Resource Environments,” _IEEE Access_, 2025.

[DOI: 10.1109/ACCESS.2025.3628960](https://doi.org/10.1109/ACCESS.2025.3628960)  
[Related GitHub repository](https://github.com/JooyoungJeong/permission-aware-rag)

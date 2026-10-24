# Office files to PDF for the document preview, so a Word, Excel or PowerPoint file shows as it is laid out
# (Gotenberg: LibreOffice behind an HTTP API). The same Noto fonts the documents are made with, so every Indian script
# renders as in the files themselves.
FROM gotenberg/gotenberg:8.37.0@sha256:f29984bd1e226bf1b93ba90af06000afa8b315853e99d27b9aaa41b93f15c769
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-core fonts-noto-extra \
 && rm -rf /var/lib/apt/lists/*
USER gotenberg

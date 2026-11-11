# One-shot document parser only. Build locally before Windows-host parsing/tests.
# Release images still require digest pinning, scanning and deployment acceptance.
FROM python:3.12-slim AS build
WORKDIR /build
COPY requirements.lock ./
RUN pip install --no-cache-dir --prefix=/install -c requirements.lock "pypdf>=6.21.4,<7" "defusedxml>=0.7,<0.8"

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends libseccomp2 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10002 --no-create-home --shell /usr/sbin/nologin parser \
 && mkdir -p /opt/parser/finagent/documents \
 && touch /opt/parser/finagent/__init__.py /opt/parser/finagent/documents/__init__.py
COPY --from=build /install /usr/local
COPY src/finagent/documents/parsers.py src/finagent/documents/parse_worker.py src/finagent/documents/network_guard.py /opt/parser/finagent/documents/
RUN cp -r /opt/parser/finagent /usr/local/lib/python3.12/site-packages/finagent
WORKDIR /opt/parser
USER 10002:10002
ENTRYPOINT ["python", "-I", "-B", "-m", "finagent.documents.parse_worker"]

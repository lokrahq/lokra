FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/lokrahq/lokra" \
      org.opencontainers.image.description="Lokra: per-agent database roles, masking, write approval and a signed audit ledger for AI agents on Postgres" \
      org.opencontainers.image.licenses="Apache-2.0"
COPY --from=build /dist /dist
RUN pip install --no-cache-dir "$(ls /dist/*.whl)[slack]" && rm -rf /dist \
 && useradd --create-home --uid 10001 lokra
USER lokra
WORKDIR /work
ENV PYTHONUNBUFFERED=1
EXPOSE 8787
ENTRYPOINT ["lokra"]
CMD ["serve", "--http", "--host", "0.0.0.0", "--port", "8787"]

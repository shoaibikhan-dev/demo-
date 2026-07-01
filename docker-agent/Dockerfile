FROM debian:bookworm-slim AS build
WORKDIR /app
COPY . .

FROM gcr.io/distroless/base-debian12:nonroot
COPY --from=build /app /app
WORKDIR /app
USER 65532:65532
ENTRYPOINT ["/app/app"]

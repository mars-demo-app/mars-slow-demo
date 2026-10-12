FROM alpine:3.20

RUN apk add --no-cache bash curl \
	&& rm -rf /var/cache/apk/*

COPY loadgen.sh /usr/local/bin/loadgen.sh
RUN chmod +x /usr/local/bin/loadgen.sh

ENTRYPOINT ["/usr/local/bin/loadgen.sh"]

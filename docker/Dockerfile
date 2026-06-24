FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 python3-pip iproute2 tcpdump iputils-ping

RUN pip3 install simpy numpy scipy

WORKDIR /app
COPY src/ .

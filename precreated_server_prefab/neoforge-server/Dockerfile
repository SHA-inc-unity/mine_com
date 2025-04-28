FROM eclipse-temurin:21-jdk

ARG SERVER_NAME
WORKDIR /server/${SERVER_NAME}

COPY . .

RUN chmod +x startserver.sh

CMD ["./startserver.sh"]

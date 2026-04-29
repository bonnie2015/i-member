FROM maven:3.8.8-eclipse-temurin-17 AS build

WORKDIR /app

COPY pom.xml .
COPY src ./src
COPY data ./data
RUN mvn -q -DskipTests package

FROM eclipse-temurin:17-jre-jammy

WORKDIR /app
COPY --from=build /app/target/scrm-0.0.1-SNAPSHOT.jar /app/app.jar
COPY data/seed /app/seed

EXPOSE 3658

CMD ["java", "-jar", "/app/app.jar"]

I have updated the `docker-compose.yml` file to address the `chroma` service failing its healthcheck.

The error `container pocketpro_nyl_chroma is unhealthy` was likely caused by the healthcheck command using `localhost`, which can be unreliable inside a Docker container. I have changed it to use `127.0.0.1` instead.

**Next Steps:**

1.  **Rebuild and start the services:**
    Open a terminal in the project root and run this command:
    ```
    docker-compose up -d --build
    ```

This should resolve the issue with the `chroma` container becoming unhealthy.
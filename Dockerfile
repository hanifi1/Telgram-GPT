FROM mambaorg/micromamba:1.5.8

WORKDIR /app

# conda/mamba env from your YAML
COPY requirements.yml /app/requirements.yml
ARG ENV_NAME=myenv
RUN micromamba create -y -n $ENV_NAME -f /app/requirements.yml && \
    micromamba clean --all --yes

# ensure env + bash
ENV MAMBA_DOCKERFILE_ACTIVATE=1
ENV MAMBA_DEFAULT_ENV=$ENV_NAME
SHELL ["/bin/bash", "-lc"]

# app code + launcher
COPY . /app
RUN chmod +x /app/start.sh

# IMPORTANT: use the launcher (so APP_ENTRY & APP_MODE work)
CMD ["bash", "-lc", "./start.sh"]





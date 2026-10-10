from setuptools import setup, find_packages

setup(
    name="oshepherd",
    version="v0.0.24",
    description="The Oshepherd guiding the Ollama(s) inference orchestration.",
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    author="mnemonica.ai",
    author_email="info@mnemonica.ai",
    packages=find_packages(include=["oshepherd", "oshepherd.*"]),
    package_data={"oshepherd.backends.supabase": ["release/manifest.json", "release/migrations/*.sql", "release/functions/ollama/*.ts", "release/templates/vercel/*", "release/templates/vercel/.vercelignore", "release/templates/vercel/.python-version"]},
    python_requires=">=3.12",
    install_requires=[
        "uvicorn",
        "fastapi[standard]",
        "celery",
        "click",
        "ollama>=0.6.3",
        "amqp",
        "redis",
        "pydantic",
        "python-dotenv",
        "requests",
    ],
    extras_require={
        'supabase': ["httpx==0.28.1", "ollama==0.6.3"],
        'dev': [
            "pytest",
            "httpx",
            "jsonschema==4.23.0",
            "black",
            "build",
            "twine"
        ],
        'tests': [
            "pytest",
            "httpx",
            "jsonschema==4.23.0"
        ],
        'lint': [
            "black"
        ]
    },
    entry_points={
        "console_scripts": [
            "oshepherd=oshepherd.cli:main",
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    project_urls={
        "Homepage": "https://github.com/mnemonica-ai/oshepherd",
        "Issues": "https://github.com/mnemonica-ai/oshepherd/issues",
    },
)

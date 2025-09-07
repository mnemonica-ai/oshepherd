from setuptools import setup, find_packages

setup(
    name="oshepherd",
    version="0.0.17",
    description="The Oshepherd guiding the Ollama(s) inference orchestration.",
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    author="mnemonica.ai",
    author_email="info@mnemonica.ai",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "gunicorn",
        "fastapi[standard]",
        "celery",
        "click",
        "ollama",
        "amqp",
        "redis",
        "pydantic",
        "python-dotenv",
        "requests",
    ],
    extras_require={
        'dev': [
            "packageName[tests, lint]",
            "build",
            "twine"
        ],
        'tests': [
            "pytest"
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

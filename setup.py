from setuptools import setup, find_packages

setup(
    name="agency-finder",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "streamlit>=1.30.0",
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "duckduckgo_search>=5.3.0",
        "pandas>=2.0.0",
        "python-dotenv>=1.0.0",
    ],
    entry_points={
        "console_scripts": [
            "agency-finder=agency_finder.cli:main",
        ],
    },
)

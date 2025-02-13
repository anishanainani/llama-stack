# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

import argparse
import asyncio
import os
import shutil
import time
from functools import partial
from pathlib import Path

import httpx

from llama_toolchain.cli.subcommand import Subcommand

from termcolor import cprint


class Download(Subcommand):
    """Llama cli for downloading llama toolchain assets"""

    def __init__(self, subparsers: argparse._SubParsersAction):
        super().__init__()
        self.parser = subparsers.add_parser(
            "download",
            prog="llama download",
            description="Download a model from llama.meta.com or Hugging Face Hub",
            formatter_class=argparse.RawTextHelpFormatter,
        )
        setup_download_parser(self.parser)


def setup_download_parser(parser: argparse.ArgumentParser) -> None:
    from llama_models.sku_list import all_registered_models

    models = all_registered_models()
    parser.add_argument(
        "--source",
        choices=["meta", "huggingface"],
        required=True,
    )
    parser.add_argument(
        "--model-id",
        choices=[x.descriptor() for x in models],
        required=True,
    )
    parser.add_argument(
        "--hf-token",
        type=str,
        required=False,
        default=None,
        help="Hugging Face API token. Needed for gated models like llama2/3. Will also try to read environment variable `HF_TOKEN` as default.",
    )
    parser.add_argument(
        "--meta-url",
        type=str,
        required=False,
        help="For source=meta, URL obtained from llama.meta.com after accepting license terms",
    )
    parser.add_argument(
        "--ignore-patterns",
        type=str,
        required=False,
        default="*.safetensors",
        help="""
For source=huggingface, files matching any of the patterns are not downloaded. Defaults to ignoring
safetensors files to avoid downloading duplicate weights.
""",
    )
    parser.set_defaults(func=partial(run_download_cmd, parser=parser))


def _hf_download(
    model: "Model",
    hf_token: str,
    ignore_patterns: str,
    parser: argparse.ArgumentParser,
):
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import GatedRepoError, RepositoryNotFoundError

    from llama_toolchain.common.model_utils import model_local_dir

    repo_id = model.huggingface_repo
    if repo_id is None:
        raise ValueError(f"No repo id found for model {model.descriptor()}")

    output_dir = model_local_dir(model)
    os.makedirs(output_dir, exist_ok=True)
    try:
        true_output_dir = snapshot_download(
            repo_id,
            local_dir=output_dir,
            ignore_patterns=ignore_patterns,
            token=hf_token,
            library_name="llama-toolchain",
        )
    except GatedRepoError:
        parser.error(
            "It looks like you are trying to access a gated repository. Please ensure you "
            "have access to the repository and have provided the proper Hugging Face API token "
            "using the option `--hf-token` or by running `huggingface-cli login`."
            "You can find your token by visiting https://huggingface.co/settings/tokens"
        )
    except RepositoryNotFoundError:
        parser.error(f"Repository '{args.repo_id}' not found on the Hugging Face Hub.")
    except Exception as e:
        parser.error(e)

    print(f"\nSuccessfully downloaded model to {true_output_dir}")


def _meta_download(model: "Model", meta_url: str):
    from llama_models.sku_list import llama_meta_net_info

    from llama_toolchain.common.model_utils import model_local_dir

    output_dir = Path(model_local_dir(model))
    os.makedirs(output_dir, exist_ok=True)

    info = llama_meta_net_info(model)

    # I believe we can use some concurrency here if needed but not sure it is worth it
    for f in info.files:
        output_file = str(output_dir / f)
        url = meta_url.replace("*", f"{info.folder}/{f}")
        total_size = info.pth_size if "consolidated" in f else 0
        cprint(f"Downloading `{f}`...", "white")
        downloader = ResumableDownloader(url, output_file, total_size)
        asyncio.run(downloader.download())

    print(f"\nSuccessfully downloaded model to {output_dir}")
    cprint(f"\nMD5 Checksums are at: {output_dir / 'checklist.chk'}", "white")


def run_download_cmd(args: argparse.Namespace, parser: argparse.ArgumentParser):
    from llama_models.sku_list import resolve_model

    model = resolve_model(args.model_id)
    if model is None:
        parser.error(f"Model {args.model_id} not found")
        return

    if args.source == "huggingface":
        _hf_download(model, args.hf_token, args.ignore_patterns, parser)
    else:
        meta_url = args.meta_url
        if not meta_url:
            meta_url = input(
                "Please provide the signed URL you received via email (e.g., https://llama3-1.llamameta.net/*?Policy...): "
            )
            assert meta_url is not None and "llama3-1.llamameta.net" in meta_url
        _meta_download(model, meta_url)


class ResumableDownloader:
    def __init__(
        self,
        url: str,
        output_file: str,
        total_size: int = 0,
        buffer_size: int = 32 * 1024,
    ):
        self.url = url
        self.output_file = output_file
        self.buffer_size = buffer_size
        self.total_size = total_size
        self.downloaded_size = 0
        self.start_size = 0
        self.start_time = 0

    async def get_file_info(self, client: httpx.AsyncClient) -> None:
        if self.total_size > 0:
            return

        # Force disable compression when trying to retrieve file size
        response = await client.head(
            self.url, follow_redirects=True, headers={"Accept-Encoding": "identity"}
        )
        response.raise_for_status()
        self.url = str(response.url)  # Update URL in case of redirects
        self.total_size = int(response.headers.get("Content-Length", 0))
        if self.total_size == 0:
            raise ValueError(
                "Unable to determine file size. The server might not support range requests."
            )

    async def download(self) -> None:
        self.start_time = time.time()
        async with httpx.AsyncClient() as client:
            await self.get_file_info(client)

            if os.path.exists(self.output_file):
                self.downloaded_size = os.path.getsize(self.output_file)
                self.start_size = self.downloaded_size
                if self.downloaded_size >= self.total_size:
                    print(f"Already downloaded `{self.output_file}`, skipping...")
                    return

            additional_size = self.total_size - self.downloaded_size
            if not self.has_disk_space(additional_size):
                print(
                    f"Not enough disk space to download `{self.output_file}`. "
                    f"Required: {(additional_size / M):.2f} MB"
                )
                raise ValueError(
                    f"Not enough disk space to download `{self.output_file}`"
                )

            while True:
                if self.downloaded_size >= self.total_size:
                    break

                # Cloudfront has a max-size limit
                max_chunk_size = 27_000_000_000
                request_size = min(
                    self.total_size - self.downloaded_size, max_chunk_size
                )
                headers = {
                    "Range": f"bytes={self.downloaded_size}-{self.downloaded_size + request_size}"
                }
                # print(f"Downloading `{self.output_file}`....{headers}")
                try:
                    async with client.stream(
                        "GET", self.url, headers=headers
                    ) as response:
                        response.raise_for_status()
                        with open(self.output_file, "ab") as file:
                            async for chunk in response.aiter_bytes(self.buffer_size):
                                file.write(chunk)
                                self.downloaded_size += len(chunk)
                                self.print_progress()
                except httpx.HTTPError as e:
                    print(f"\nDownload interrupted: {e}")
                    print("You can resume the download by running the script again.")
                except Exception as e:
                    print(f"\nAn error occurred: {e}")

            print(f"\nFinished downloading `{self.output_file}`....")

    def print_progress(self) -> None:
        percent = (self.downloaded_size / self.total_size) * 100
        bar_length = 50
        filled_length = int(bar_length * self.downloaded_size // self.total_size)
        bar = "█" * filled_length + "-" * (bar_length - filled_length)

        elapsed_time = time.time() - self.start_time
        M = 1024 * 1024  # noqa

        speed = (
            (self.downloaded_size - self.start_size) / (elapsed_time * M)
            if elapsed_time > 0
            else 0
        )
        print(
            f"\rProgress: |{bar}| {percent:.2f}% "
            f"({self.downloaded_size // M}/{self.total_size // M} MB) "
            f"Speed: {speed:.2f} MiB/s",
            end="",
            flush=True,
        )

    def has_disk_space(self, file_size: int) -> bool:
        dir_path = os.path.dirname(os.path.abspath(self.output_file))
        free_space = shutil.disk_usage(dir_path).free
        return free_space > file_size

"""Private fixed-operation JSONL worker. No authority, user environment or shell."""

import contextlib
import json
import sys
from typing import Literal

from jarvis.platforms.filesystem.documents import read_document
from jarvis.platforms.filesystem.limits import limit_worker
from jarvis.platforms.filesystem.sandbox import evaluate
from jarvis.platforms.filesystem.storage import search, write
from jarvis.security.filesystem_policy import FilePolicy
from jarvis.tools.base import ToolError, ToolModel
from jarvis.tools.filesystem import EvalResult, ReadDocument, SandboxEval, Search, WriteFile

MAX_REQUEST = 131072
MAX_REPLY = 262144


class Request(ToolModel):
    operation: Literal["fs.search", "fs.read_document", "fs.write_file", "python.sandbox_eval"]
    policy: FilePolicy
    arguments: str


def main() -> None:
    output = sys.stdout.buffer
    reply: dict[str, object]
    try:
        limit_worker()
        raw = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
            raise ToolError("file_limit")
        request = Request.model_validate_json(raw)
        result: ToolModel
        with contextlib.redirect_stdout(sys.stderr):
            match request.operation:
                case "fs.search":
                    result = search(request.policy, Search.model_validate_json(request.arguments))
                case "fs.read_document":
                    result = read_document(
                        request.policy, ReadDocument.model_validate_json(request.arguments)
                    )
                case "fs.write_file":
                    result = write(request.policy, WriteFile.model_validate_json(request.arguments))
                case "python.sandbox_eval":
                    args = SandboxEval.model_validate_json(request.arguments)
                    value, stdout = evaluate(args.code, args.data)
                    result = EvalResult(value_json=value, stdout=stdout)
        reply = {"result": result.model_dump(mode="json")}
    except FileNotFoundError:
        reply = {"error": "file_missing"}
    except FileExistsError:
        reply = {"error": "file_exists"}
    except (PermissionError, NotADirectoryError):
        reply = {"error": "file_denied"}
    except UnicodeError:
        reply = {"error": "file_unsupported"}
    except (MemoryError, RecursionError):
        reply = {"error": "file_limit"}
    except ToolError as error:
        reply = {"error": error.code}
    except Exception:
        reply = {"error": "file_failure"}
    encoded = json.dumps(reply, ensure_ascii=False).encode()
    if len(encoded) > MAX_REPLY:
        encoded = b'{"error":"file_limit"}'
    output.write(encoded + b"\n")
    output.flush()


if __name__ == "__main__":
    main()

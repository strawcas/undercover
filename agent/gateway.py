"""AgentCore Gateway tool names, request signing and refusal handling."""

import json
import os
import re
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from langchain.tools import ToolException
from mcp.shared.exceptions import McpError

from agent.backend_client import Refused
from agent.mcp_client import McpClient

APP = "undercover-app"
CONTEXT = "undercover-context"
SERVICE = "bedrock-agentcore"
DENIED = "Tool Execution Denied"
APP_ERROR = re.compile(
    r"^(?:Client|Server) error: API request failed with status: (\d+) - (.*)$",
    re.DOTALL,
)


class GatewayDenied(ToolException):
    """The Gateway Policy refused the tool call."""


class GatewayAuth(httpx.Auth):
    """Sign every MCP request using the current AWS CLI credentials."""

    requires_request_body = True

    def __init__(self, session: boto3.Session) -> None:
        self.session = session

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        credentials = self.session.get_credentials()
        region = self.session.region_name
        if credentials is None or not region:
            raise RuntimeError("AgentCore Gateway needs AWS credentials and a region")
        headers = {"Host": request.url.netloc.decode("ascii")}
        if content_type := request.headers.get("content-type"):
            headers["Content-Type"] = content_type
        signed = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=headers,
        )
        SigV4Auth(credentials.get_frozen_credentials(), SERVICE, region).add_auth(
            signed
        )
        for header in ("Authorization", "X-Amz-Date", "X-Amz-Security-Token"):
            if header in signed.headers:
                request.headers[header] = signed.headers[header]
        yield request


def tool_name(target: str, operation: str) -> str:
    """Return the name given to the operation by the Gateway."""
    return f"{target}___{operation}"


@dataclass(frozen=True)
class Gateway:
    """Use one signed MCP endpoint for the Gateway's targets."""

    client: McpClient

    def call(self, target: str, operation: str, **arguments: Any) -> Any:
        """Call a target and distinguish its refusal from a Policy denial."""
        try:
            return self.client.call(tool_name(target, operation), arguments)
        except McpError as error:
            if str(error).startswith(DENIED):
                raise GatewayDenied(str(error)) from error
            raise
        except ToolException as error:
            if target == APP and (match := APP_ERROR.match(str(error))):
                status, body = match.groups()
                try:
                    detail = json.loads(body).get("detail", body)
                except (ValueError, AttributeError):
                    detail = body
                raise Refused(int(status), detail) from error
            raise


def configured() -> Gateway | None:
    """Use the Gateway when its URL is set at graph construction."""
    if url := os.getenv("UNDERCOVER_GATEWAY_URL"):
        return Gateway(McpClient(url, GatewayAuth(boto3.Session())))
    return None

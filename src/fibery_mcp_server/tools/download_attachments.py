import os
from typing import Dict, Any, List

import mcp

from fibery_mcp_server.fibery_client import FiberyClient

download_attachments_tool_name = "download_attachments"


def download_attachments_tool() -> mcp.types.Tool:
    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "descriptions", "download_attachments"), "r"
    ) as file:
        description = file.read()

    return mcp.types.Tool(
        name=download_attachments_tool_name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": 'The entity type in "Space/Type" format (e.g., "Cricket/Player", "Product Management/Item")',
                },
                "entity_id": {
                    "type": "string",
                    "description": "The fibery/id of the entity to get attachments from",
                },
                "attachment_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: List of specific attachment names to get download links for. If not provided, returns links for all attachments.",
                },
                "attachment_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: List of specific attachment IDs to get download links for. If not provided, returns links for all attachments.",
                },
                "file_secrets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: List of file secrets to get download links for directly (for inline files from markdown pattern /api/files/SECRET). When provided, entity_type and entity_id are ignored.",
                },
            },
            "required": [],
        },
    )


async def handle_download_attachments(
    fibery_client: FiberyClient, arguments: Dict[str, Any]
) -> List[mcp.types.TextContent]:
    entity_type = arguments.get("entity_type")
    entity_id = arguments.get("entity_id")
    attachment_names = arguments.get("attachment_names", [])
    attachment_ids = arguments.get("attachment_ids", [])
    file_secrets = arguments.get("file_secrets", [])

    # If file_secrets are provided, handle inline files
    if file_secrets:
        return await _handle_inline_files(fibery_client, file_secrets)

    # Otherwise, require entity_type and entity_id for attachment queries
    if not entity_type or not entity_id:
        return [
            mcp.types.TextContent(
                type="text", text="Either file_secrets or both entity_type and entity_id must be provided"
            )
        ]

    # Query the entity to get all its attachments
    query = {
        "q/from": entity_type,
        "q/select": [
            "fibery/id",
            {"Files/Files": {"q/select": ["fibery/secret", "fibery/name", "fibery/id"], "q/limit": "q/no-limit"}},
        ],
        "q/where": ["=", ["fibery/id"], "$entity-id"],
        "q/limit": 1,
    }

    params = {"$entity-id": entity_id}

    command_result = await fibery_client.query(query, params)

    if not command_result.success:
        return [mcp.types.TextContent(type="text", text=f"Error querying entity: {command_result}")]

    if not command_result.result:
        return [mcp.types.TextContent(type="text", text=f"Entity with ID {entity_id} not found in {entity_type}")]

    entity = command_result.result[0]
    files = entity.get("Files/Files", [])

    if not files:
        return [mcp.types.TextContent(type="text", text=f"No attachments found for entity {entity_id}")]

    # Filter attachments if specific names or IDs are requested
    filtered_files = files
    if attachment_names or attachment_ids:
        filtered_files = []
        for file in files:
            if attachment_names and file["fibery/name"] in attachment_names:
                filtered_files.append(file)
            elif attachment_ids and file["fibery/id"] in attachment_ids:
                filtered_files.append(file)

    if not filtered_files:
        filter_info = ""
        if attachment_names:
            filter_info += f" with names {attachment_names}"
        if attachment_ids:
            filter_info += f" with IDs {attachment_ids}"
        return [mcp.types.TextContent(type="text", text=f"No attachments found{filter_info}")]

    # Generate download links
    download_links = []
    base_url = f"https://{fibery_client._FiberyClient__fibery_host}/api/files"

    for file in filtered_files:
        download_url = f"{base_url}/{file['fibery/secret']}"
        download_links.append(
            {
                "name": file["fibery/name"],
                "id": file["fibery/id"],
                "secret": file["fibery/secret"],
                "download_url": download_url,
            }
        )

    result_text = f"Found {len(download_links)} attachment(s) for entity {entity_id}:\n\n"
    for link in download_links:
        result_text += f"Name: {link['name']}\n"
        result_text += f"ID: {link['id']}\n"
        result_text += f"Download URL: {link['download_url']}\n"
        result_text += f"Curl command: curl -H 'Authorization: Token YOUR_FIBERY_TOKEN' '{link['download_url']}' -o '{link['name']}'\n\n"

    return [mcp.types.TextContent(type="text", text=result_text)]


async def _handle_inline_files(fibery_client: FiberyClient, file_secrets: List[str]) -> List[mcp.types.TextContent]:
    """Handle inline files by making GET requests to get redirect URLs."""
    import httpx

    download_links = []
    base_url = f"https://{fibery_client._FiberyClient__fibery_host}/api/files"

    # Create HTTP client with auth header
    headers = {"Authorization": f"Token {fibery_client._FiberyClient__fibery_api_token}"}

    async with httpx.AsyncClient(follow_redirects=False) as client:
        for secret in file_secrets:
            file_url = f"{base_url}/{secret}"

            try:
                # Make GET request to get redirect
                response = await client.get(file_url, headers=headers)

                if response.status_code in [301, 302, 303, 307, 308]:
                    # Extract redirect URL from Location header
                    redirect_url = response.headers.get("Location")
                    if redirect_url:
                        download_links.append(
                            {
                                "secret": secret,
                                "fibery_url": file_url,
                                "download_url": redirect_url,
                            }
                        )
                    else:
                        download_links.append(
                            {
                                "secret": secret,
                                "fibery_url": file_url,
                                "error": "No redirect URL found",
                            }
                        )
                elif response.status_code == 200:
                    # Direct access, no redirect needed
                    download_links.append(
                        {
                            "secret": secret,
                            "fibery_url": file_url,
                            "download_url": file_url,
                        }
                    )
                else:
                    download_links.append(
                        {
                            "secret": secret,
                            "fibery_url": file_url,
                            "error": f"HTTP {response.status_code}: {response.text}",
                        }
                    )
            except Exception as e:
                download_links.append(
                    {
                        "secret": secret,
                        "fibery_url": file_url,
                        "error": f"Request failed: {str(e)}",
                    }
                )

    # Format results
    result_text = f"Found {len(download_links)} file(s):\n\n"
    for link in download_links:
        result_text += f"Secret: {link['secret']}\n"
        result_text += f"Fibery URL: {link['fibery_url']}\n"

        if "error" in link:
            result_text += f"Error: {link['error']}\n"
        else:
            result_text += f"Download URL: {link['download_url']}\n"
            result_text += f"Curl command: curl '{link['download_url']}' -o 'file_{link['secret']}'\n"

        result_text += "\n"

    return [mcp.types.TextContent(type="text", text=result_text)]

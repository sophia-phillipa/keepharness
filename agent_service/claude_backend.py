"""Claude Code stream-json adapter. Only the scoped MCP server supplies tools."""
import asyncio
import json
import time
from .tools import ToolError


class Stream:
    def __init__(self, event):
        self.event = event
        self.answer = ''
        self.thinking = ''
        self.result = None
        self.tools = {}
        self.started = time.monotonic()
        self.first = None

    def consume(self, item):
        kind = item.get('type')
        if kind == 'stream_event':
            value = item.get('event', {})
            block = value.get('content_block', {})
            if value.get('type') == 'content_block_start' and block.get('type') == 'tool_use':
                self.tools[block['id']] = block.get('name', 'tool')
                self.event('tool_start', {'tool':block.get('name'), 'tool_id':block['id']})
            delta = value.get('delta', {})
            if delta.get('type') == 'text_delta':
                text = delta.get('text', '')
                self.answer += text
                if text and self.first is None:
                    self.first = time.monotonic() - self.started
                self.event('answer_delta', {'text':text})
            elif delta.get('type') == 'thinking_delta':
                text = delta.get('thinking', '')
                self.thinking += text
                self.event('reasoning_delta', {'text':text})
        elif kind == 'user':
            for block in item.get('message', {}).get('content', []):
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    tid = block.get('tool_use_id')
                    self.event('tool_end', {'tool':self.tools.pop(tid, 'tool'), 'tool_id':tid,
                               'status':'failed' if block.get('is_error') else 'completed'})
        elif kind == 'result':
            if item.get('is_error') or item.get('subtype') != 'success':
                raise ToolError('claude_execution_failed')
            self.result = item
        if len(self.answer) + len(self.thinking) > 500000:
            raise ToolError('claude_output_limit')

    def finish(self, model):
        if self.result is None:
            raise ToolError('claude_stream_incomplete')
        usage = self.result.get('usage', {})
        answer = self.result.get('result', self.answer)
        if not self.answer and answer:
            self.event('answer_delta', {'text':answer})
        return {'answer':answer, 'backend':'claude', 'model':model, 'effort':'configured',
                'cloud_inference':True, 'finish_reason':'completed', 'incomplete':False,
                'context_strategy':'replayed_history',
                'metrics':{'input_tokens':usage.get('input_tokens'),
                           'output_tokens':usage.get('output_tokens'),
                           'cached_tokens':usage.get('cache_read_input_tokens'),
                           'cache_creation_tokens':usage.get('cache_creation_input_tokens'),
                           'ttft_seconds':self.first,
                           'inference_seconds':time.monotonic() - self.started,
                           'billing':'Claude account; quota unavailable'}}


async def stream(command, prompt, event, model):
    state = Stream(event)
    proc = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=1024*1024)
    async def write_prompt():
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()
    writer = asyncio.create_task(write_prompt())
    event('planning', {'backend':'claude', 'model':model, 'effort':'configured'})
    size = 0
    try:
        async for line in proc.stdout:
            size += len(line)
            if size > 8*1024*1024:
                raise ToolError('claude_output_limit')
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError()
            except ValueError:
                raise ToolError('claude_invalid_stream') from None
            state.consume(item)
        await writer
        if await proc.wait() != 0:
            raise ToolError('claude_execution_failed')
        return state.finish(model)
    finally:
        writer.cancel()
        await asyncio.gather(writer, return_exceptions=True)
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

"""Synthetic source corpus shared by isolated and batched sender regressions."""
from typing import Final

RAW_SOURCES: Final = {
    "bound-local": "def notify(client):\n send = client.send_owner_dm\n send(raw)",
    "annotated-chain": "def notify(client):\n first: object = client.send_owner_dm\n send = first\n send(raw)",
    "positional-default": "def notify(send=client.send_owner_dm): send(raw)",
    "keyword-default": "def notify(*, send=client.send_owner_dm): send(raw)",
    "positional-callback": "def forward(send, body, /): send(body)\ndef notify(client): forward(client.send_owner_dm, raw)",
    "keyword-callback": "def forward(*, send, body): send(body)\ndef notify(client): forward(send=client.send_owner_dm, body=raw)",
    "positional-client": "def forward(transport, body):\n send = transport.send_owner_dm\n send(body)\ndef notify(client): forward(client, raw)",
    "attribute-alias": "def notify(client, holder):\n holder.emit = client.send_owner_dm\n holder.emit(raw)",
    "attribute-forwarding": "def forward(holder, body): holder.emit(body)\ndef notify(client, box):\n box.emit = client.send_owner_dm\n forward(box, raw)",
    "wrapper-alias": "def forward(send, body): send(body)\ndef notify(client):\n wrapper = forward\n send = client.send_owner_dm\n wrapper(send, raw)",
    "nested-wrapper": "def notify(client):\n send = client.send_owner_dm\n def forward(): send(raw)\n forward()",
    "conditional-alias": "def notify(client):\n send = unrelated\n if enabled: send = client.send_owner_dm\n send(raw)",
    "attribute-wrapper": "def forward(send, body): send(body)\ndef notify(client, box):\n box.forward = forward\n box.forward(client.send_owner_dm, raw)",
    "literal-getattr": "def notify(client):\n send = getattr(client, 'send_owner_dm')\n send(raw)",
}

FINITE: Final = {
    "tuple": "def notify(client):\n send, = (client.send_owner_dm,)\n send('raw')",
    "nested-list": "def notify(client):\n [unused, [send]] = [None, [client.send_owner_dm]]\n send('raw')",
    "starred-assignment": "def notify(client):\n first, *tail = (None, client.send_owner_dm)\n tail[0]('raw')",
    "lambda-default": "def notify(client):\n forward = lambda send=client.send_owner_dm: send('raw')\n forward()",
    "lambda-callback": "def notify(client):\n forward = lambda send: send('raw')\n forward(client.send_owner_dm)",
    "local-getattr": "def notify(client):\n name = 'send_owner_dm'\n send = getattr(client, name)\n send('raw')",
    "conditional-getattr": "def notify(client, flag):\n name = 'send_owner_dm' if flag else 'log'\n alias = name\n getattr(client, alias)('raw')",
    "star-args": "def forward(send, body): send(body)\ndef notify(client):\n args = (client.send_owner_dm, 'raw')\n forward(*args)",
    "star-keywords": "def forward(*, send, body): send(body)\ndef notify(client):\n kwargs = {'send': client.send_owner_dm, 'body': 'raw'}\n forward(**kwargs)",
    "subscript": "def notify(client):\n callbacks = {'send': client.send_owner_dm}\n callbacks['send']('raw')",
    "subscript-write": "def notify(client):\n callbacks = {}\n callbacks['send'] = client.send_owner_dm\n callbacks['send']('raw')",
    "returned-callback": "def choose(client): return client.send_owner_dm\ndef notify(client):\n send = choose(client)\n send('raw')",
    "bound-method": "class Helper:\n def forward(self, send, body): send(body)\ndef notify(client):\n helper = Helper()\n helper.forward(client.send_owner_dm, 'raw')",
    "cross-method": "class Helper:\n def __init__(self, client): self.emit = client.send_owner_dm\n def notify(self): self.emit('raw')\ndef notify(client): Helper(client).notify()",
    "global": "def setup(client):\n global send\n send = client.send_owner_dm\ndef notify(): send('raw')",
    "nonlocal": "def notify(client):\n send = None\n def setup():\n  nonlocal send\n  send = client.send_owner_dm\n setup()\n send('raw')",
}

OPAQUE: Final = {
    "external-callback": "def notify(client, external): external(client.send_owner_dm, 'raw')",
    "opaque-return": "def notify(client, factory):\n send = factory(client.send_owner_dm)\n send('raw')",
    "opaque-unpack": "def forward(*args): args[0]('raw')\ndef notify(client, external):\n args = external(client.send_owner_dm)\n forward(*args)",
}

LOOPS: Final = {
    "tuple-loop": "for dispatch in (client.send_owner_dm,):\n  dispatch('raw')",
    "list-loop": "for dispatch in [client.send_owner_dm]:\n  dispatch('raw')",
    "enumerate-loop": "for index, dispatch in enumerate((client.send_owner_dm,)):\n  dispatch('raw')",
    "zip-loop": "for dispatch, label in zip([client.send_owner_dm], ['owner']):\n  dispatch('raw')",
    "dict-values-loop": "callbacks = {'owner': client.send_owner_dm}\n for dispatch in callbacks.values():\n  dispatch('raw')",
    "dict-items-loop": "for label, dispatch in {'owner': client.send_owner_dm}.items():\n  dispatch('raw')",
    "reassigned-loop": "for dispatch in (client.send_owner_dm,):\n  dispatch = client.log\n  dispatch('raw')",
    "comprehension": "[dispatch('raw') for dispatch in (client.send_owner_dm,)]",
    "nested-destructuring-loop": "for [label, (dispatch,)] in [('owner', (client.send_owner_dm,))]:\n  dispatch('raw')",
    "named-loop": "callbacks = (client.send_owner_dm,)\n for dispatch in callbacks:\n  dispatch('raw')",
    "conditional-loop": "for dispatch in ([client.send_owner_dm] if flag else [client.log]):\n  dispatch('raw')",
    "union-loop": "callbacks = [client.log]\n callbacks = [client.send_owner_dm]\n for dispatch in callbacks:\n  dispatch('raw')",
}

PATTERNS: Final = {
    "sequence-match": "match (client.send_owner_dm,):\n  case (dispatch,): dispatch('raw')",
    "capture-match": "match client.send_owner_dm:\n  case dispatch: dispatch('raw')",
    "mapping-match": "match {'send': client.send_owner_dm}:\n  case {'send': dispatch}: dispatch('raw')",
    "nested-match": "match {'send': [client.send_owner_dm]}:\n  case {'send': [dispatch]}: dispatch('raw')",
    "as-match": "match (client.send_owner_dm,):\n  case (dispatch,) as callbacks: callbacks[0]('raw')",
    "or-match": "match (client.send_owner_dm,):\n  case [dispatch] | [None, dispatch]: dispatch('raw')",
    "star-match": "match (None, client.send_owner_dm, None):\n  case (_, *callbacks, _): callbacks[0]('raw')",
    "rest-match": "match {'send': client.send_owner_dm, 'label': None}:\n  case {'label': _, **callbacks}: callbacks['send']('raw')",
    "named-match": "callbacks = (client.send_owner_dm,)\n match callbacks:\n  case (dispatch,): dispatch('raw')",
}

UNMODELLED: Final = {
    "boolean-selection": ("dispatch = client.send_owner_dm or client.log\n dispatch('raw')", "BoolOp.values"),
    "augmented-container": ("callbacks = []\n callbacks += [client.send_owner_dm]\n callbacks[0]('raw')", "AugAssign.value"),
}

HELPERS: Final = {
    "from-import": ("from helper import forward", "forward"),
    "module-import": ("import helper as h", "h.forward"),
    "relative-import": ("from .helper import forward as emit", "emit"),
    "package-import": ("from . import helper as h", "h.forward"),
    "literal-import-module": ("from importlib import import_module\nh = import_module('helper')", "h.forward"),
}

"""Pinned snapshot of the suite model index.

``model-index.json`` is the serial 10 bundled snapshot of
``Mitroshenkov87/voxprint-model-index``. ``model-index.pin.json`` records
head ``15edeab0``, serial 10, and ``generated_at``. ``index-public.pem`` is
the Ed25519 trust anchor. A remote index replaces the snapshot only after
its detached signature verifies and it is at least this pin. Serial 10 is
newer than the live signed serial-9 remote, so the bundle is used until
Model Forge publishes a signed index at serial 10 or later.
"""

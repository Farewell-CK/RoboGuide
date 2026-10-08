# Shared-world integrated relocation deployment profile

This directory contains a deployment-owned, offline-validated Fetch/Stretch
shared-world template for the generic `object.relocate@v1` operation. Both Nodes
retain their mobility operations and add the same integrated relocation workflow,
capacity-one `space` resource, local simulator lock, readiness route, and explicit
Habitat robot type. The planning profile exposes only abstract capability facts;
the execution profile exposes operation resource needs.

The files are templates for the next controlled preflight. They do not select
physical entities, invent payload limits, or constitute a benchmark result. The
current Episode51 navigation runner remains unchanged until a separate preflight
wiring change is reviewed.

### Title
Unprivileged Callers Can Spam `PriceQuery` Events via Public `getPriceX18` — (File: `core/contracts/Endpoint.sol`)

---

### Summary
`Endpoint.getPriceX18` is a `public` state-mutating function with no access control that emits a `PriceQuery` event on every call. Any unprivileged address can invoke it at will, generating an unbounded stream of `PriceQuery` events that off-chain services (sequencer, indexers, price oracles) may interpret as legitimate protocol price-query activity.

---

### Finding Description
`getPriceX18` is declared `public override` in `Endpoint.sol` and emits `PriceQuery(productId)` unconditionally on every invocation:

```solidity
function getPriceX18(uint32 productId)
    public
    override
    returns (int128 _priceX18)
{
    _priceX18 = priceX18[productId];
    require(_priceX18 != 0, ERR_INVALID_PRODUCT);
    emit PriceQuery(productId);   // ← emitted for every caller, no guard
}
``` [1](#0-0) 

There is no `onlySequencer`, `onlyEndpoint`, or any other access modifier. The function is not `view` — it is a state-mutating call that writes to the event log. Any EOA or contract can call it with any valid `productId` and cause `PriceQuery` to be emitted.

The `PriceQuery` event is defined in the `IEndpoint` interface and is consumed by off-chain infrastructure (sequencer, indexers) that tracks price-query activity across the protocol. The same event is also emitted inside `BaseEngine` and `PerpEngine`, where it is gated behind internal protocol flows. [2](#0-1) 

---

### Impact Explanation
An attacker can:
1. Call `getPriceX18(productId)` in a tight loop (or via a contract) for any registered product.
2. Flood the event log with `PriceQuery` events that are indistinguishable from legitimate protocol-generated ones.
3. Cause off-chain services that consume `PriceQuery` events to misinterpret spurious queries as real protocol demand signals, potentially corrupting price-feed heuristics, analytics, or sequencer-side logic that reacts to query frequency.

The corrupted state is the **off-chain event stream** — the same class of impact described in the reference report, where spurious events "can be somehow interpreted by off-chain services."

---

### Likelihood Explanation
Likelihood is **high**. The function is `public`, requires no tokens, no role, and no prior state. Any address can call it with zero cost beyond gas. The only prerequisite is knowing a valid `productId`, which is trivially discoverable from on-chain state or the ABI.

---

### Recommendation
Remove the `emit PriceQuery(productId)` side-effect from the externally callable getter, or restrict the event-emitting variant to `internal` visibility and expose a separate `view` function for external price reads:

```solidity
// Pure read — no event
function getPriceX18(uint32 productId)
    public
    view
    override
    returns (int128 _priceX18)
{
    _priceX18 = priceX18[productId];
    require(_priceX18 != 0, ERR_INVALID_PRODUCT);
}

// Internal — emits event only when called by trusted protocol paths
function _emitPriceQuery(uint32 productId) internal {
    emit PriceQuery(productId);
}
```

This mirrors the recommendation in the reference report: move the side-effecting logic to `internal` so only trusted protocol paths can trigger the event.

---

### Proof of Concept
1. Deploy or connect to the live `Endpoint` contract.
2. Call `getPriceX18(1)` (or any valid `productId`) from an arbitrary EOA.
3. Observe `PriceQuery(1)` emitted in the transaction receipt — no authorization required.
4. Repeat in a loop or from a contract to generate arbitrarily many `PriceQuery` events, polluting the event log consumed by off-chain services. [1](#0-0)

### Citations

**File:** core/contracts/Endpoint.sol (L334-342)
```text
    function getPriceX18(uint32 productId)
        public
        override
        returns (int128 _priceX18)
    {
        _priceX18 = priceX18[productId];
        require(_priceX18 != 0, ERR_INVALID_PRODUCT);
        emit PriceQuery(productId);
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L1-10)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

import "./clearinghouse/IClearinghouse.sol";

interface IEndpoint {
    event SubmitTransactions();
    event PriceQuery(uint32 productId);

    // events that we parse transactions into
```

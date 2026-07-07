### Title
Missing Zero-Address Validation for `_clearinghouseLiq` in `Clearinghouse.initialize` — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary
The `Clearinghouse.initialize` function stores the `_clearinghouseLiq` address without any non-zero validation. If this address is set to `address(0)` at deployment, all liquidation calls will silently fail or revert at the call site, leaving unhealthy accounts unliquidatable and exposing the protocol to insolvency.

---

### Finding Description
In `Clearinghouse.sol`, the `initialize` function accepts `_clearinghouseLiq` and assigns it directly to storage without a `require(_clearinghouseLiq != address(0))` guard:

```solidity
// core/contracts/Clearinghouse.sol L25-L40
function initialize(
    address _endpoint,
    address _quote,
    address _clearinghouseLiq,
    uint256 _spreads,
    address _withdrawPool
) external initializer {
    __Ownable_init();
    setEndpoint(_endpoint);
    quote = _quote;
    clearinghouse = address(this);
    clearinghouseLiq = _clearinghouseLiq;   // ← no zero-address check
    spreads = _spreads;
    withdrawPool = _withdrawPool;
    emit ClearinghouseInitialized(_endpoint, _quote);
}
```

`clearinghouseLiq` is the address of the `ClearinghouseLiq` contract, which is the sole executor of all liquidation logic. Every liquidation path in `Clearinghouse.sol` delegates to this address. If it is `address(0)`, every liquidation call will revert at the delegatecall/call boundary, with no on-chain indication of the misconfiguration at initialization time.

The same `initialize` function also stores `_quote` and `_withdrawPool` without zero-address guards, but `_clearinghouseLiq` is the most critical because it controls the protocol's solvency safety mechanism.

---

### Impact Explanation
If `clearinghouseLiq` is `address(0)`:
- Every call to liquidate an unhealthy subaccount reverts.
- Underwater accounts accumulate bad debt that can never be resolved.
- The insurance fund cannot be drawn down via the normal liquidation path.
- Protocol solvency degrades silently until the misconfiguration is discovered and a redeployment/upgrade is performed.

This is a direct analog to the Linea fallback-operator bug: both are critical safety-mechanism addresses stored in an initializer without zero-address validation, and both cause the safety mechanism to be permanently broken if the zero address is passed.

---

### Likelihood Explanation
Likelihood is low-to-medium. The initializer is called once at deployment by the deployer/proxy admin, so exploitation requires a deployment error (passing `address(0)` for `_clearinghouseLiq`). However, because there is no on-chain guard, such an error would not be caught at initialization time and would only surface when the first liquidation is attempted — potentially after significant user funds have been deposited.

---

### Recommendation
Add an explicit non-zero check for all critical address parameters in `Clearinghouse.initialize`:

```solidity
require(_clearinghouseLiq != address(0), "clearinghouseLiq is zero");
require(_quote != address(0), "quote is zero");
require(_withdrawPool != address(0), "withdrawPool is zero");
```

Apply the same pattern to `Endpoint.initialize` for `_sequencer`, `_offchainExchange`, and `_verifier`, and to `BaseEngine._initialize` for all address parameters.

---

### Proof of Concept

1. Deploy `Clearinghouse` proxy and call `initialize` with `_clearinghouseLiq = address(0)`.
2. No revert occurs; `clearinghouseLiq` is silently set to `address(0)`.
3. Users deposit collateral and open leveraged positions normally.
4. A subaccount's health drops below maintenance margin.
5. A liquidator calls the liquidation entry point on `Clearinghouse`.
6. The call delegates to `clearinghouseLiq` (i.e., `address(0)`), which reverts.
7. The unhealthy account cannot be liquidated; bad debt accumulates indefinitely. [1](#0-0) [2](#0-1)

### Citations

**File:** core/contracts/Clearinghouse.sol (L25-40)
```text
    function initialize(
        address _endpoint,
        address _quote,
        address _clearinghouseLiq,
        uint256 _spreads,
        address _withdrawPool
    ) external initializer {
        __Ownable_init();
        setEndpoint(_endpoint);
        quote = _quote;
        clearinghouse = address(this);
        clearinghouseLiq = _clearinghouseLiq;
        spreads = _spreads;
        withdrawPool = _withdrawPool;
        emit ClearinghouseInitialized(_endpoint, _quote);
    }
```

**File:** core/contracts/ClearinghouseStorage.sol (L1-10)
```text
// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity ^0.8.0;

import "./interfaces/engine/IProductEngine.sol";
import "./interfaces/engine/ISpotEngine.sol";
import "./interfaces/engine/IPerpEngine.sol";

abstract contract ClearinghouseStorage {
    using MathSD21x18 for int128;

```

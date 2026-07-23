### Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any non-allowlisted user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address, not the end-user. A pool admin who allowlists the router (the only way to permit any router-mediated swap on an allowlisted pool) inadvertently grants every user on-chain the ability to bypass the per-user allowlist gate.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router is the entity that calls `pool.swap()`. Therefore `sender` = router address, not the end-user. The allowlist lookup becomes `allowedSwapper[pool][router]`.

This creates the same dual-identity structural flaw as the BundlerRegistry report:

| Path | Identity checked | Who it represents |
|---|---|---|
| Direct `pool.swap()` | end-user | end-user ✓ |
| Via `MetricOmmSimpleRouter` | router address | every user on-chain ✗ |

A pool admin who wants any router-mediated swap to succeed on an allowlisted pool **must** call `setAllowedToSwap(pool, router, true)`. The moment they do, the allowlist is effectively disabled for all users, because any non-allowlisted user can route through the same public router contract.

The `DepositAllowlistExtension` does **not** share this flaw: `beforeAddLiquidity` checks the `owner` parameter, which the liquidity adder passes as the actual user, not the adder's own address. [4](#0-3) 

---

### Impact Explanation

Any non-allowlisted user can execute swaps against a pool that the admin intended to restrict. This breaks the core allowlist invariant and allows unauthorized parties to trade against pool liquidity, potentially front-running allowlisted LPs, draining bins, or executing swaps the pool operator explicitly prohibited. This is a direct loss-of-control over pool access with fund-impacting consequences (unauthorized swap settlement against LP assets).

---

### Likelihood Explanation

The trigger is a valid, expected admin action: allowlisting the router so that allowlisted users can swap through the standard periphery. Any pool that uses `SwapAllowlistExtension` and also wants router support is affected. No privileged attacker role is required — any public user can call `MetricOmmSimpleRouter` once the router is allowlisted.

---

### Recommendation

The `beforeSwap` hook should gate the **end-user**, not the direct caller. Two options:

1. **Pass the end-user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted router assumption.

2. **Check `recipient` instead of `sender`**: If the pool's swap design guarantees `recipient` is the economic beneficiary, gate on that. Verify this holds for all router call patterns.

3. **Dedicated router allowlist + per-user check inside the router**: The extension allowlists the router as a trusted forwarder and the router enforces its own per-user allowlist before calling the pool.

The cleanest fix matching the BundlerRegistry recommendation (use one canonical identifier) is option 1 or 2: collapse the two identities (direct caller vs. router) into one canonical end-user identity that the allowlist always checks.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {Test} from "forge-std/Test.sol";
import {SwapAllowlistExtension} from
    "metric-periphery/contracts/extensions/SwapAllowlistExtension.sol";
import {AllowlistFactoryStub} from "metric-periphery/test/AllowlistFactoryStub.sol";

contract SwapAllowlistBypassTest is Test {
    AllowlistFactoryStub factoryStub;
    SwapAllowlistExtension extension;
    address pool    = makeAddr("pool");
    address admin   = makeAddr("admin");
    address router  = makeAddr("router");   // MetricOmmSimpleRouter
    address badUser = makeAddr("badUser");  // NOT on the allowlist

    function setUp() public {
        factoryStub = new AllowlistFactoryStub();
        factoryStub.setPoolAdmin(pool, admin);
        extension = new SwapAllowlistExtension(address(factoryStub));

        // Admin allowlists the router so that allowlisted users can swap via router
        vm.prank(admin);
        extension.setAllowedToSwap(pool, router, true);

        // badUser is explicitly NOT allowlisted
        assertFalse(extension.isAllowedToSwap(pool, badUser));
    }

    function test_nonAllowlistedUserBypassesViaRouter() public {
        // Pool calls beforeSwap; sender = router (router called pool.swap())
        // badUser is the actual end-user but is invisible to the extension
        vm.prank(pool);
        // Does NOT revert — badUser bypasses the allowlist via the router
        extension.beforeSwap(
            router,     // sender = router, not badUser
            badUser,    // recipient (ignored by the check)
            false, 0, 0, 0, 0, 0, ""
        );
    }
}
```

The test passes without revert, demonstrating that `badUser` — who is not on the allowlist — can execute a swap on a restricted pool simply by routing through `MetricOmmSimpleRouter`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L151-177)
```text
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

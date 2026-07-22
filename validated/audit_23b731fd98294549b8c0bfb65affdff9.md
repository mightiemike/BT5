### Title
SwapAllowlistExtension Gates Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `sender` is the router's address, not the user's. If the pool admin allowlists the router so that permitted users can reach the pool through the standard periphery path, every non-permitted user can bypass the restriction by routing through the same contract.

---

### Finding Description

**How the allowlist check is wired**

`SwapAllowlistExtension.beforeSwap` reads:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the contract that called the extension). `sender` is the first argument forwarded by the pool. [1](#0-0) 

**How the pool sets `sender`**

Inside `MetricOmmPool.swap`, the pool passes its own `msg.sender` — the direct caller of `pool.swap()` — as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [2](#0-1) 

**How the router calls the pool**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [4](#0-3) 

**The impossible choice forced on the pool admin**

The extension stores allowlist entries keyed by `(pool, swapper)`. When a swap arrives through the router, the checked identity is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin therefore faces two equally broken options:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the standard periphery path at all |
| **Allowlist the router** | Every non-allowlisted user bypasses the gate by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

---

### Impact Explanation

The `SwapAllowlistExtension` is the only on-chain mechanism for restricting who may trade against a pool's LP positions. A complete bypass means:

- Non-permitted addresses (e.g., non-KYC'd, sanctioned, or bot addresses) can execute swaps against restricted pools.
- LPs who deposited under the assumption that only vetted counterparties could trade against them are exposed to the full universe of callers.
- Any value-extracting swap (large price impact, MEV, sandwich) that the allowlist was meant to block can now be executed freely.

This is a direct loss-of-LP-principal risk: the guard that was supposed to protect LP funds is silently inoperative for every user who routes through the standard periphery.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the documented, standard swap interface for the protocol.
- A pool admin who wants allowlisted users to be able to trade will naturally allowlist the router, triggering the bypass.
- No special privilege, flash loan, or unusual token behavior is required — any EOA can call the router.
- The bypass is silent: the extension returns `IMetricOmmExtensions.beforeSwap.selector` normally; no event or revert signals that the wrong identity was checked.

---

### Recommendation

**Short term:** Document that allowlisting the router address grants unrestricted swap access to all router users, and that pools using `SwapAllowlistExtension` must require direct `pool.swap()` calls (no router). Add a revert in the extension if `sender` matches any known router address.

**Long term:** Redesign the identity forwarding so the pool passes the economically relevant actor — the address that will pay for the swap — rather than the direct `msg.sender`. One approach: the router encodes the original `msg.sender` into `extensionData` and the extension verifies it against a router-signed or factory-registered claim. A cleaner approach is a dedicated `originalSender` field in the pool's swap interface that the router populates with `msg.sender` before calling the pool.

---

### Proof of Concept

```
Setup
─────
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Pool admin calls setAllowedToSwap(pool, router, true)  // so alice can use the router

Attack
──────
4. Bob (non-KYC'd) calls:
       router.exactInputSingle({
           pool:      restrictedPool,
           recipient: bob,
           zeroForOne: true,
           amountIn:  X,
           ...
       })

5. Router calls pool.swap(bob, true, X, ...) with msg.sender = router.

6. Pool calls extension.beforeSwap(sender=router, ...).

7. Extension evaluates:
       allowedSwapper[pool][router]  →  true   ✓ (step 3)
   → no revert, swap proceeds.

8. Bob's swap executes against LP funds despite Bob never being allowlisted.
```

The root cause is identical in structure to the Fuji index bug: a guard reads a stale/wrong identity (`router` instead of `user`) because the update step that would produce the correct value (`sender` = original user) is never performed on the router-mediated path.

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

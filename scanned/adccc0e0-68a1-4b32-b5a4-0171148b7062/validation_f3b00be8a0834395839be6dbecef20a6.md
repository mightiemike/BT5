### Title
`SwapAllowlistExtension` gates on the router's address instead of the end user's address, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the **router contract**, not the end user. The extension therefore checks whether the router is allowlisted, not whether the actual trader is allowlisted. Any user who routes through the public router bypasses the per-pool swap allowlist entirely.

---

### Finding Description

**Allowlist check (extension):** [1](#0-0) 

`sender` is the first argument forwarded by the pool. The extension checks `allowedSwapper[msg.sender /* pool */][sender]`.

**Pool passes `msg.sender` as `sender`:** [2](#0-1) 

So `sender` = whoever called `pool.swap()`.

**Router calls `pool.swap()` as itself:** [3](#0-2) 

The router stores the real user in transient storage for the payment callback, but calls `pool.swap()` directly — making `msg.sender` to the pool the **router address**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Consequence — two broken states:**

1. **Bypass**: If the pool admin allowlists the router address (e.g., to let any approved user reach the pool through the router), every unpermissioned user can also call `router.exactInputSingle` and the extension passes because the router is allowlisted.

2. **Broken periphery**: If the pool admin only allowlists individual user addresses, those users cannot use the router at all (the router is not allowlisted), breaking the supported periphery path for legitimate traders.

Neither configuration lets the pool admin achieve the intended policy: "only specific users may swap, including through the router."

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that protection the moment the router is allowlisted. Any public user can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutput` and trade against the pool's liquidity without being on the allowlist. LP funds are exposed to unrestricted trading that the pool admin explicitly configured the extension to prevent.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery path for end users. Pool admins who want allowlisted users to have a normal UX will allowlist the router. The bypass is then immediately reachable by any public caller with no special privileges, no flash loan, and no multi-step setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **end user**, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through the router**: Have the router forward the real `msg.sender` as an extra field in `extensionData`, and have the extension decode and check that address. This requires a convention between the router and the extension.

2. **Check `sender` against a router-aware allowlist**: The extension can detect that `sender` is a known router and, in that case, require the extension payload to carry the real user address (signed or otherwise authenticated).

The simplest correct fix is to not allowlist the router at all and require allowlisted users to call the pool directly — but this breaks the supported periphery path and is not a viable long-term solution.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (extension1 = SwapAllowlistExtension, beforeSwap order = 1)
  pool admin calls: swapExtension.setAllowedToSwap(pool, address(router), true)
    (admin does this so that Alice, an allowlisted user, can use the router)

Attack:
  Bob (address NOT in allowedSwapper[pool]) calls:
    router.exactInputSingle(ExactInputSingleParams{
        pool: pool,
        zeroForOne: true,
        amountIn: X,
        ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
          msg.sender to pool = address(router)
        → _beforeSwap(sender=address(router), ...)
          → SwapAllowlistExtension.beforeSwap(sender=address(router), ...)
              allowedSwapper[pool][router] == true  ← passes
        → swap executes, Bob receives tokens

Result: Bob, who is not on the allowlist, successfully swaps against the curated pool.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

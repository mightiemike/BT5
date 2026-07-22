### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. If the pool admin allowlists the router address (the only way to let allowlisted users reach the pool through the router), every user — including those not on the allowlist — can bypass the guard by routing through the same public router.

---

### Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

In `MetricOmmPool.swap()`, the pool calls:

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` then encodes that value as the `sender` argument forwarded to every configured extension.

**Step 2 — `SwapAllowlistExtension` checks `sender`, which is the router.**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

**Step 3 — `MetricOmmSimpleRouter` calls `pool.swap()` directly, making itself `msg.sender`.**

```solidity
// MetricOmmSimpleRouter.exactInputSingle()
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

The router never forwards the original `msg.sender` (the end user) to the pool. The pool therefore passes `address(router)` as `sender` to the extension.

**Step 4 — The allowlist check resolves to `allowedSwapper[pool][router]`.**

For allowlisted users to reach the pool through the router, the pool admin must add the router to the allowlist. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every caller — including users who are not individually allowlisted — because the extension cannot distinguish between them.

**The two failure modes are mutually exclusive and both harmful:**

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use the router; broken UX |
| Yes | Any user bypasses the allowlist via the router |

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified market makers, institutional partners) loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()` targeting the pool and execute swaps at oracle-anchored prices. LPs who deposited under the assumption that only vetted counterparties would trade against them are exposed to adversarial flow, potential oracle-price arbitrage, and value extraction that the allowlist was designed to prevent.

---

### Likelihood Explanation

The router is the primary public swap interface documented in the protocol. A pool admin who wants allowlisted users to be able to use the router has no alternative but to add the router to the allowlist. This is a natural, expected configuration step, making the bypass reachable in any real deployment of a curated pool that also supports router-mediated swaps.

---

### Recommendation

The extension must check the actual economic actor, not the intermediary. Two viable approaches:

1. **Pass the end user through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData`; the extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `sender` only when it is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, require the end user's address to be supplied and verified via `extensionData`.

Either approach ensures the allowlist gates the economically relevant actor regardless of which supported periphery path reaches the pool.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, address(router), true)
   — necessary so that allowlisted users can reach the pool through the router.
3. Non-allowlisted user (attacker) calls:
     MetricOmmSimpleRouter.exactInputSingle({
         pool: pool,
         tokenIn: token0,
         recipient: attacker,
         amountIn: X,
         ...
     })
4. Router calls pool.swap(...) with msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. Extension evaluates: allowedSwapper[pool][router] == true → passes.
7. Swap executes. Attacker receives token1 output at oracle price.
   The allowlist check never saw the attacker's address.
```

**Relevant code locations:**

- `sender` binding: [1](#0-0) 
- Extension dispatch: [2](#0-1) 
- Allowlist check on `sender`: [3](#0-2) 
- Router calls pool directly (no user forwarding): [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

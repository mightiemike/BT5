### Title
`SwapAllowlistExtension` Allowlist Bypassed via `MetricOmmSimpleRouter`: Any Unprivileged User Can Swap in Gated Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which equals `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of that call, so the extension checks the **router's address** instead of the **actual user's address**. Because the router is a public, permissionless contract, any user who routes through it is checked as if they were the router. If the pool admin allowlists the router (the only way to let any allowlisted user trade through it), the gate is open to every address on-chain.

---

### Finding Description

`SwapAllowlistExtension` is designed to restrict which addresses may swap in a pool. Its `beforeSwap` hook receives `sender` — the value the pool passes from its own `msg.sender` — and checks it against a per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool populates `sender` with its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every address on-chain can bypass the allowlist via the router |

There is no configuration that simultaneously permits allowlisted users to trade through the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

**High.** The `SwapAllowlistExtension` is the primary mechanism for pools that need to restrict trading to specific counterparties (e.g., KYC'd users, institutional LPs, whitelisted market makers). Once the router is allowlisted, any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` and trade against the pool's liquidity without restriction. LP funds are directly exposed to counterparties the pool admin explicitly intended to exclude. The pool's core access-control invariant — that only allowlisted addresses may swap — is fully broken for all router-mediated paths.

---

### Likelihood Explanation

**High.** The router is a public, permissionless contract. No special privilege, timing, or negligence is required. Any user who discovers the pool uses `SwapAllowlistExtension` can immediately route through `MetricOmmSimpleRouter` to bypass the gate. The bypass is structural, not probabilistic.

---

### Recommendation

The extension must gate the **originating user**, not the intermediary contract. Two viable approaches:

1. **Pass the real user through `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension verify it. This requires the extension to trust the pool's `sender` field only for direct calls and fall back to a signed/encoded identity for router calls — complex and fragile.

2. **Check `tx.origin` as a fallback** (only acceptable if the pool is not used in contract-to-contract flows): Replace `sender` with `tx.origin` inside the extension when `sender` is a known router. This is brittle and generally discouraged.

3. **Preferred — enforce identity at the router level**: Add a `msg.sender`-forwarding mechanism. The router should pass the real caller's address in a standardized field of `extensionData`, and the extension should decode and verify it, rejecting calls where the encoded identity does not match a signed or trusted source.

4. **Document and warn**: At minimum, document clearly that `SwapAllowlistExtension` only gates direct `pool.swap()` calls and is ineffective for router-mediated swaps, so pool admins do not deploy it under the false assumption that it covers all swap paths.

---

### Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension E
  - Pool admin calls E.setAllowedToSwap(P, alice, true)
  - Pool admin calls E.setAllowedToSwap(P, router, true)  ← required for alice to use the router
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient=bob, ...) — router is msg.sender
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[P][router] → true
  5. Swap proceeds; bob receives output tokens from the pool

Result:
  - bob, an explicitly non-allowlisted address, successfully swaps against the restricted pool
  - The allowlist guard is fully bypassed
  - LP funds are exposed to an unauthorized counterparty
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
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

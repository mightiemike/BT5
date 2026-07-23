### Title
`SwapAllowlistExtension` Gates on Router Address Instead of End-User — Allowlist Fully Bypassed When Router Is Permitted - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `swap` call. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the end-user. If the pool admin allowlists the router so that legitimate users can trade through it, every non-allowlisted address can also bypass the gate by calling the same public router. The allowlist is rendered meaningless.

---

### Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly: [4](#0-3) 

The pool's `msg.sender` is therefore the **router**, not the end-user. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for any pool admin who deploys `SwapAllowlistExtension`:

- **If the router is NOT allowlisted**: every allowlisted user's router-mediated swap reverts, breaking the primary supported swap path.
- **If the router IS allowlisted**: every non-allowlisted address can call `exactInputSingle` / `exactInput` / `exactOutput` through the public router and bypass the gate entirely, because the check resolves to `allowedSwapper[pool][router] == true` for all of them.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner supplied by the caller), not on `sender`: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker pays no special cost beyond gas. The pool's curation policy is completely nullified, allowing arbitrary addresses to drain liquidity at oracle prices that the pool admin intended to expose only to trusted parties. This is a direct loss of LP assets and a broken core pool invariant.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary, publicly documented swap entrypoint. Any pool that deploys `SwapAllowlistExtension` and allowlists the router (the only way to let legitimate users use the router) immediately opens the bypass to all users. The attack requires no special knowledge, no privileged access, and no front-running — a single `exactInputSingle` call suffices.

---

### Recommendation

`SwapAllowlistExtension` must gate on the **end-user identity**, not the direct pool caller. Two approaches:

1. **Preferred — pass original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The extension decodes and checks that address. The pool admin allowlists end-user addresses, not the router.

2. **Alternative — check `tx.origin`**: Use `tx.origin` as the swapper identity inside the extension. This is simpler but incompatible with smart-contract wallets and multi-sig signers.

The `DepositAllowlistExtension` pattern (gating on `owner`, a caller-supplied but position-attributed address) shows the correct design intent: the checked identity must be the economically relevant actor, not the intermediary contract.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that allowlisted users can trade through the router).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. attacker (non-allowlisted EOA) calls:
       MetricOmmSimpleRouter.exactInputSingle({
         pool: guardedPool,
         tokenIn: token0,
         ...
       })
  2. Router calls pool.swap(recipient, ...) — pool's msg.sender = router.
  3. Pool calls _beforeSwap(router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  5. Swap executes at oracle price; attacker receives token1.

Result:
  - Non-allowlisted attacker successfully swaps on a curated pool.
  - Every non-allowlisted address can repeat this indefinitely.
  - The allowlist provides zero protection for router-mediated swaps.
```

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

### Title
`SwapAllowlistExtension` checks the router's address instead of the actual swapper, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. If the pool admin allowlists the router (which is required for any router-mediated swap to succeed), every user — including those not on the allowlist — can bypass the gate by simply calling the router instead of the pool directly.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

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

`msg.sender` here is the pool (correct), and `sender` is the value the pool forwards as the first argument to `_beforeSwap`. That value is `msg.sender` of the pool's own `swap` call — i.e., whoever called `pool.swap(...)`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

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

The pool's `msg.sender` is the router contract. The pool then passes `sender = router_address` to `ExtensionCalling._beforeSwap`, which encodes it into the extension call. The extension therefore evaluates `allowedSwapper[pool][router_address]` — not `allowedSwapper[pool][actual_user]`.

The same pattern holds for `exactInput` (multi-hop), `exactOutputSingle`, and `exactOutput` — in every case the router is `msg.sender` of the pool's `swap` call.

The pool admin faces an impossible choice:
- **Allowlist the router** → every user can bypass the allowlist by routing through it.
- **Do not allowlist the router** → no user can use the router at all, even allowlisted ones.

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks them for non-allowlisted users.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for restricting swap access to specific addresses (e.g., KYC-verified counterparties, whitelisted market participants, or institutional LPs). A bypass means:

- Any unprivileged user can execute swaps on a pool that the admin intended to restrict.
- Restricted pools may hold concentrated liquidity at oracle-anchored prices; unauthorized swappers can drain the favorable side of the book.
- Protocol fees and LP principal are exposed to actors the pool admin explicitly excluded.

This is a direct loss-of-principal / broken-core-functionality impact: the allowlist guard — the only access-control mechanism on the swap path — is rendered ineffective for all router-mediated swaps.

---

### Likelihood Explanation

- The router is a public, permissionless contract. Any user can call it.
- The bypass requires no special privileges, no flash loan, and no complex setup — just calling `exactInputSingle` on the router instead of `pool.swap` directly.
- Pools that deploy `SwapAllowlistExtension` and also want to support the standard periphery router (the expected production configuration) are automatically vulnerable.
- Likelihood is **High**.

---

### Recommendation

The extension must gate the **original user**, not the intermediary. Two sound approaches:

1. **Pass the real swapper via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires the router to be trusted to supply honest data, which can be enforced by also checking that `sender` (the router) is a known factory-registered router.

2. **Check `sender` only when `sender` is not a known router**: Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real swapper from `extensionData`; otherwise check `sender` directly.

Either way, the extension must be updated so that the identity it checks against the allowlist is the economic actor initiating the swap, not the contract that relays it.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — required for any router swap to work.
3. Pool admin calls `setAllowedToSwap(pool, alice, false)` — Alice is not allowlisted.
4. Alice calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → **passes**.
8. Alice's swap executes on the restricted pool despite not being on the allowlist.

The check at `SwapAllowlistExtension.sol` line 37 (`allowedSwapper[msg.sender][sender]`) passes because `sender` is the router, which the admin was forced to allowlist to enable any router usage at all. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```

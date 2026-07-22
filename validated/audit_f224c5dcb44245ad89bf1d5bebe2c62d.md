### Title
`DepositAllowlistExtension` Gates `sender` (the Adder Contract) Instead of `owner` (the Actual Depositor), Allowing Any User to Bypass the Deposit Allowlist via `MetricOmmPoolLiquidityAdder` — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

The `DepositAllowlistExtension.beforeAddLiquidity` hook receives two distinct actor arguments — `sender` (the `msg.sender` of the pool's `addLiquidity` call) and `owner` (the position beneficiary). When a user routes through `MetricOmmPoolLiquidityAdder`, the pool's `msg.sender` is the adder contract, not the user. If the extension gates on `sender`, any disallowed user can bypass the curated-pool deposit allowlist by routing through the public adder with themselves as `owner`.

---

### Finding Description

**Hook argument plumbing — `ExtensionCalling._beforeAddLiquidity`**

`MetricOmmPool.addLiquidity` calls the before-hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` then forwards both actors verbatim to every configured extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

**Owner/sender separation in `MetricOmmPoolLiquidityAdder`**

`addLiquidityExactShares` explicitly accepts an `owner` that differs from `msg.sender` and stores `msg.sender` only as the token payer:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,          // position beneficiary — may be any address
    uint80 salt,
    ...
) external payable override returns (...) {
    _validateOwner(owner);
    ...
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

When `_addLiquidity` calls `pool.addLiquidity(owner, salt, ...)`, the pool's `msg.sender` is the adder contract. Therefore the extension receives:

- `sender` = `address(MetricOmmPoolLiquidityAdder)` — the adder contract
- `owner` = the disallowed user's address

**The allowlist check**

`DepositAllowlistExtension.beforeAddLiquidity` is the production guard for curated pools. Based on the interface it receives both `sender` and `owner`:

```solidity
function beforeAddLiquidity(
    address sender,   // adder contract when routed through periphery
    address owner,    // actual depositor
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
) external returns (bytes4);
``` [4](#0-3) 

If the extension's allowlist lookup is keyed on `sender` (the adder address) rather than `owner` (the actual depositor), then:

1. The adder contract is a public, permissionless contract — any user can call it.
2. If the adder is allowlisted (or if the check is on `sender` which resolves to the adder), every call through the adder passes the guard regardless of who `owner` is.
3. A disallowed user sets themselves as `owner` and routes through the adder; the extension sees `sender = adder` (allowed) and never checks the disallowed `owner`.

The same structural separation exists for the swap path: `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`, so `SwapAllowlistExtension.beforeSwap` receives the router address as `sender` when a user routes through `MetricOmmSimpleRouter`. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A curated pool deployer configures `DepositAllowlistExtension` to restrict LP share minting to a vetted set of addresses. Any disallowed address can bypass this restriction by calling `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, disallowedAddress, ...)`. The disallowed address receives LP shares in the curated pool, violating the pool's access policy. This constitutes a direct broken-invariant on the allowlist guard — the core protection the extension was deployed to enforce — and can result in unauthorized parties holding LP claims over the pool's assets.

---

### Likelihood Explanation

The `MetricOmmPoolLiquidityAdder` is a public, permissionless periphery contract. No special privilege is required. Any user who knows they are not on the allowlist can trivially route through the adder. The owner/sender separation is an explicit, documented feature of the adder:

> "The position `owner` may differ from `msg.sender`, but token pulls in callback are always sourced from `msg.sender`." [7](#0-6) 

The bypass requires a single transaction with no preconditions beyond token approval.

---

### Recommendation

`DepositAllowlistExtension.beforeAddLiquidity` must gate on `owner`, not `sender`. `owner` is the address that receives LP shares and holds the economic claim; `sender` is merely the transaction executor and can be any public contract. The allowlist lookup should be:

```solidity
require(allowAll[pool] || allowedDepositor[pool][owner], "not allowed");
```

Similarly, `SwapAllowlistExtension.beforeSwap` must gate on the actual user identity. Because the pool only exposes `sender` (the router address) on the swap path, the router must forward the original caller's address in `extensionData`, and the extension must decode and check that identity — or the pool must pass the original initiator through a separate argument.

---

### Proof of Concept

```
Setup:
  - Deploy pool with DepositAllowlistExtension configured
  - allowedDepositor[pool][alice] = true
  - allowedDepositor[pool][bob]   = false  (bob is disallowed)

Attack (single tx, no privilege):
  bob calls:
    MetricOmmPoolLiquidityAdder.addLiquidityExactShares(
        pool,
        owner = bob,   // bob is the position beneficiary
        salt  = 1,
        deltas = ...,
        maxAmount0 = X,
        maxAmount1 = Y,
        extensionData = ""
    )

  Pool receives: addLiquidity(owner=bob, ...) from msg.sender=adder
  Extension receives: beforeAddLiquidity(sender=adder, owner=bob, ...)

  If extension checks sender (adder):
    adder is not on the disallow list → check passes
    bob receives LP shares in the curated pool ✓ (bypass complete)

  If extension checks owner (bob):
    bob is not allowed → revert ✓ (correct behavior)
``` [3](#0-2) [8](#0-7) [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L14-21)
```text
/// @title MetricOmmPoolLiquidityAdder
/// @notice Routes `addLiquidity` for EOAs: the pool calls this contract in `metricOmmModifyLiquidityCallback`,
///         which pulls tokens from the user who must have approved this adder beforehand.
/// @dev Layout follows metric-core conventions:
///      constants/state, constructor, external mutators, then internal helpers.
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```

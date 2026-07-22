### Title
`DepositAllowlistExtension` Checks `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` parameter and validates the `owner` (LP position recipient) instead. Because `owner` is a free caller-controlled argument to `addLiquidity`, any address — regardless of allowlist status — can pass the guard by supplying an already-allowlisted address as `owner`.

---

### Finding Description

`MetricOmmPool.addLiquidity` calls the extension hook with two distinct addresses:

```solidity
// MetricOmmPool.sol line 191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` is the actual depositor (the entity providing tokens via the callback); `owner` is the LP-position recipient chosen by the caller.

`DepositAllowlistExtension.beforeAddLiquidity` receives both but discards `sender` (first parameter is unnamed):

```solidity
// DepositAllowlistExtension.sol lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`msg.sender` inside the extension is the calling pool (correct for the pool-identity key), but the depositor identity check uses `owner` — a value the caller supplies freely — instead of the `sender` argument that carries the real `msg.sender` of `addLiquidity`.

The admin configures the allowlist with the intent of restricting depositors:

```solidity
// DepositAllowlistExtension.sol lines 18-20
function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
```

But the check never reads `allowedDepositor[pool][sender]`; it reads `allowedDepositor[pool][owner]`.

---

### Impact Explanation

Any unprivileged address can bypass the deposit allowlist by calling:

```
pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)
```

The extension sees `allowedDepositor[pool][allowlistedAddress] = true` and passes. The actual caller (not allowlisted) provides tokens through the swap callback; the LP shares are minted to `allowlistedAddress`. The allowlist — the pool admin's primary access-control mechanism for restricting who may provide liquidity — is rendered completely ineffective. This is a broken core pool functionality and an admin-boundary break: an unprivileged path bypasses a pool-admin-configured guard.

---

### Likelihood Explanation

- Trigger requires only a standard `addLiquidity` call with `owner` set to any allowlisted address.
- No special permissions, flash loans, or privileged access needed.
- The pool's `addLiquidity` imposes no restriction on who may set `owner` to an arbitrary address.
- Any pool deploying `DepositAllowlistExtension` with a non-open allowlist is immediately vulnerable.

---

### Recommendation

Replace the unnamed first parameter with `sender` and validate it instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

Update `isAllowedToDeposit` and the admin setter NatDoc to clarify that the checked address is the `addLiquidity` caller, not the LP-position owner.

---

### Proof of Concept

1. Deploy a pool with `DepositAllowlistExtension` configured in `beforeAddLiquidity` order.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` and leaves `allowAllDepositors[pool] = false`.
3. Bob (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. Bob's callback transfers tokens into the pool; alice receives LP shares.
6. Bob has deposited into a restricted pool without being allowlisted — the guard is fully bypassed. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-19)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
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

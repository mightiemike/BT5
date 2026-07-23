### Title
`DepositAllowlistExtension` checks LP position `owner` instead of the actual depositing `sender`, allowing any unpermissioned address to bypass the deposit guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument (the address that actually funds the deposit and receives the callback) and instead gates on `owner` (the LP position recipient). Because `MetricOmmPool.addLiquidity` lets any caller specify an arbitrary `owner`, an unpermissioned address can bypass the allowlist entirely by naming any already-allowlisted address as the position owner.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes two distinct addresses into the extension hook chain:

```
_beforeAddLiquidity(msg.sender /*sender*/, owner /*owner*/, salt, deltas, extensionData);
```

`sender` is the address that called `addLiquidity`, pays the tokens via the swap-callback mechanism, and is the only address whose permission is meaningful for a deposit gate. `owner` is merely the beneficiary of the resulting LP shares.

`DepositAllowlistExtension.beforeAddLiquidity` explicitly discards `sender`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  lines 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The unnamed first parameter (`address`) is the actual depositor; it is never read. The guard only verifies that `owner` is on the allowlist.

Because `addLiquidity` imposes no constraint tying `msg.sender` to `owner`, any unpermissioned address `B` can call:

```
pool.addLiquidity(allowlisted_address_A, salt, deltas, callbackData, extensionData)
```

The extension sees `owner = A` (allowlisted → check passes), while `B` funds the deposit and the pool accepts tokens from an address the admin never approved.

---

### Impact Explanation

The deposit allowlist is the primary mechanism for pools that need to restrict who can provide liquidity (e.g., permissioned or regulated pools). Bypassing it means:

1. **Unauthorized funds enter the pool** — the pool admin's restriction is completely ineffective against any caller who knows one allowlisted address.
2. **Pool state is altered without consent** — the unauthorized depositor can shift `curPosInBin`, change bin balances, and affect the marginal price seen by subsequent swappers.
3. **Allowlisted address receives unwanted LP exposure** — the named `owner` accumulates shares they did not initiate; while they can remove them, the pool has already been modified.

This breaks the core invariant of the extension: *only allowlisted addresses may deposit*.

---

### Likelihood Explanation

- No special privilege is required; any EOA or contract can call `pool.addLiquidity`.
- Allowlisted addresses are publicly readable from `allowedDepositor` (a public mapping).
- The attacker only needs to name one such address as `owner` and supply the tokens themselves.
- The attack is repeatable every block with no cooldown.

---

### Recommendation

Replace the unnamed first parameter with `sender` and gate on it instead of (or in addition to) `owner`:

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

If the intent is to gate both the funder and the position owner, both addresses should be checked.

---

### Proof of Concept

1. Pool `P` is deployed with `DepositAllowlistExtension` configured; `allowAllDepositors[P] = false`.
2. Admin calls `setAllowedToDeposit(P, alice, true)`. Bob is **not** allowlisted.
3. Bob calls `P.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
4. The pool calls `extension.beforeAddLiquidity(bob, alice, ...)`.
5. The extension checks `allowedDepositor[P][alice]` → `true` → no revert.
6. Bob's tokens are pulled via callback; Alice receives LP shares; Bob has successfully deposited into a restricted pool without being on the allowlist. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

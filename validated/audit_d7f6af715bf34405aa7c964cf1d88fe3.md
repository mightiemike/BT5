### Title
`BaseWithdrawPool.verifier` Cannot Be Updated After Initialization, Causing Stale Verifier Desynchronization in Fast Withdrawal Signature Validation — (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool` stores the `verifier` address once during `_initialize` with no subsequent update mechanism. The `Endpoint` contract stores its own independent `verifier` reference in `EndpointStorage`. If the protocol ever updates the verifier (e.g., to patch a signature validation bug), `WithdrawPool` continues using the stale, potentially vulnerable verifier for all fast withdrawal signature checks, while the rest of the protocol operates on the new one.

---

### Finding Description

`BaseWithdrawPool._initialize` stores the verifier address into a private state variable: [1](#0-0) 

```solidity
function _initialize(address _clearinghouse, address _verifier)
    internal
    initializer
{
    __Ownable_init();
    clearinghouse = _clearinghouse;
    verifier = _verifier;
}
```

This `verifier` is then used directly in `submitFastWithdrawal` to validate all fast withdrawal signatures: [2](#0-1) 

```solidity
Verifier v = Verifier(verifier);
v.requireValidTxSignatures(transaction, idx, signatures);
```

There is no `setVerifier()` or equivalent function anywhere in `BaseWithdrawPool` or `WithdrawPool`. The entire contract has no mechanism to update this address post-initialization. [3](#0-2) 

Meanwhile, `EndpointStorage` stores its own independent `verifier` reference: [4](#0-3) 

```solidity
IVerifier internal verifier;
```

These two `verifier` storage slots are initialized independently and have no synchronization mechanism between them. The protocol's upgradeability architecture (proxy pattern managed by `ProxyManager`) is designed to allow implementation upgrades, and a verifier update is a realistic operational event (e.g., to fix a signature validation flaw or rotate signing keys). [5](#0-4) 

---

### Impact Explanation

If the protocol updates the `verifier` in `Endpoint` (e.g., to patch a known signature bypass), `WithdrawPool` continues using the old, potentially exploitable verifier for `submitFastWithdrawal`. An attacker who knows the old verifier's weakness can forge fast withdrawal signatures that the new verifier would reject, and submit them to `WithdrawPool.submitFastWithdrawal()` — which is callable by any unprivileged address — to drain tokens from the pool.

Conversely, if the old verifier is decommissioned (keys rotated), all legitimate fast withdrawals through `WithdrawPool` would fail validation, causing a permanent DoS on the fast withdrawal path.

The corrupted state is: `BaseWithdrawPool.verifier` (line 34) diverges from the protocol-canonical verifier, breaking the security invariant that all signature validation uses the same trusted verifier instance. [6](#0-5) 

---

### Likelihood Explanation

The Nado protocol is explicitly designed for upgrades — `ProxyManager` manages a full upgrade lifecycle for `Endpoint`, `Clearinghouse`, and their sub-implementations. A verifier update is a realistic operational event (key rotation, bug fix, or feature upgrade). When it occurs, the `WithdrawPool` verifier will silently remain stale with no on-chain indication of the desync. The `submitFastWithdrawal` function is permissionlessly callable, so any actor can exploit the window between the verifier update in `Endpoint` and a manual (currently impossible) update in `WithdrawPool`.

---

### Recommendation

Add a `setVerifier(address _verifier)` function to `BaseWithdrawPool` restricted to `onlyOwner`, mirroring the pattern used for `setWithdrawPool` in `Clearinghouse`: [7](#0-6) 

```solidity
function setVerifier(address _verifier) external onlyOwner {
    require(_verifier != address(0));
    verifier = _verifier;
}
```

Alternatively, instead of caching the verifier address, `BaseWithdrawPool` should resolve it dynamically from the `Clearinghouse` or `Endpoint` at call time, ensuring it always uses the canonical verifier.

---

### Proof of Concept

1. Protocol deploys `WithdrawPool` with `verifier = V1`.
2. `Endpoint` is also initialized with `verifier = V1`.
3. A signature validation bug is discovered in `V1`. Protocol deploys `V2` and updates `Endpoint.verifier = V2`.
4. `WithdrawPool.verifier` remains `V1` — there is no `setVerifier()` to call.
5. Attacker calls `WithdrawPool.submitFastWithdrawal(idx, transaction, forgedSigs)` with signatures crafted to exploit the known `V1` bug.
6. `Verifier(verifier).requireValidTxSignatures(...)` runs against `V1`, accepts the forged signatures.
7. `handleWithdrawTransfer` executes, transferring tokens to the attacker's address. [6](#0-5)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L23-30)
```text
    function _initialize(address _clearinghouse, address _verifier)
        internal
        initializer
    {
        __Ownable_init();
        clearinghouse = _clearinghouse;
        verifier = _verifier;
    }
```

**File:** core/contracts/BaseWithdrawPool.sol (L32-34)
```text
    address internal clearinghouse;

    address internal verifier;
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
    }
```

**File:** core/contracts/EndpointStorage.sol (L63-63)
```text
    IVerifier internal verifier;
```

**File:** core/contracts/BaseProxyManager.sol (L27-72)
```text
contract ProxyManagerHelper {
    address internal proxyManager;
    address internal clearinghouse;
    address internal endpoint;

    modifier onlyOwner() {
        require(
            msg.sender == proxyManager,
            "only proxyManager can access to ProxyManagerHelper."
        );
        _;
    }

    constructor() {
        proxyManager = msg.sender;
    }

    function registerClearinghouse(address _clearinghouse) external onlyOwner {
        clearinghouse = _clearinghouse;
    }

    function registerEndpoint(address _endpoint) external onlyOwner {
        endpoint = _endpoint;
    }

    function getClearinghouseLiq() external view returns (address) {
        return IIClearinghouse(clearinghouse).getClearinghouseLiq();
    }

    function upgradeClearinghouseLiq(address clearinghouseLiq)
        external
        onlyOwner
    {
        IIClearinghouse(clearinghouse).upgradeClearinghouseLiq(
            clearinghouseLiq
        );
    }

    function getEndpointTx() external view returns (address) {
        return IIEndpointUpgradeable(endpoint).getEndpointTx();
    }

    function upgradeEndpointTx(address _endpointTx) external onlyOwner {
        IIEndpointUpgradeable(endpoint).upgradeEndpointTx(_endpointTx);
    }
}
```

**File:** core/contracts/Clearinghouse.sol (L750-753)
```text
    function setWithdrawPool(address _withdrawPool) external onlyOwner {
        require(_withdrawPool != address(0));
        withdrawPool = _withdrawPool;
    }
```

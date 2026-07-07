### Title
Immutable `deployer` Address in `ContractOwner` Is a Single-Point of Failure with No Transfer Mechanism — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.sol` defines a `deployer` role that is set once during `initialize()` and can never be changed. Unlike the `owner` role (which inherits `OwnableUpgradeable` and thus has `transferOwnership()`), the `deployer` address has no transfer or revocation mechanism. This makes the `deployer` key a permanent single-point of failure for critical product management operations.

---

### Finding Description

`ContractOwner` stores a `deployer` address that is assigned exactly once: [1](#0-0) [2](#0-1) 

The `onlyDeployer` modifier enforces exclusive access for this address: [3](#0-2) 

The following functions are gated exclusively behind `onlyDeployer` with no alternative access path:

- `submitSpotAddOrUpdateProductCall()` — queues a new spot product configuration
- `submitPerpAddOrUpdateProductCall()` — queues a new perp product configuration
- `clearSpotAddOrUpdateProductCalls()` — clears pending spot product calls
- `clearPerpAddOrUpdateProductCalls()` — clears pending perp product calls
- `delistProduct()` — submits a product delisting transaction [4](#0-3) [5](#0-4) 

There is no `setDeployer()`, `transferDeployer()`, or any other function in the entire contract that can update the `deployer` variable. The `owner` (multisig) has no ability to reassign the deployer role either.

Contrast this with the `owner` role, which is properly initialized via `OwnableUpgradeable` and immediately transferred to a multisig: [6](#0-5) 

The `deployer` role has no equivalent protection.

---

### Impact Explanation

**Lost deployer key**: The protocol permanently loses the ability to:
1. Queue new spot or perp product configurations — `addOrUpdateProducts()` (owner-gated) can never be called because it depends on the deployer having first populated `rawSpotAddOrUpdateProductCalls` / `rawPerpAddOrUpdateProductCalls`.
2. Delist products — no perp product can ever be settled and closed.

This means the protocol cannot expand to new trading pairs and cannot perform emergency product delisting, permanently degrading protocol functionality for all users.

**Compromised deployer key**: An attacker can:
1. Submit malicious product configurations with extreme risk parameters (e.g., near-zero margin weights) into the pending queue, potentially tricking the owner into approving them.
2. Clear legitimate pending product calls, blocking protocol upgrades indefinitely. [7](#0-6) 

---

### Likelihood Explanation

Key management failures (loss, rotation after personnel changes, hardware failure) are a well-documented operational risk in DeFi protocols. The `deployer` is a single EOA or contract address with no redundancy. Because there is no on-chain recovery path, any key management failure is permanent and irreversible. Likelihood is **medium** given the operational lifetime of the protocol.

---

### Recommendation

Add a `setDeployer(address newDeployer)` function gated by `onlyOwner` to allow the multisig to rotate the deployer address:

```solidity
function setDeployer(address newDeployer) external onlyOwner {
    require(newDeployer != address(0), "zero address");
    deployer = newDeployer;
}
```

This mirrors the pattern already used for the `submitter` role in `BaseProxyManager`, which correctly provides `updateSubmitter()`: [8](#0-7) 

---

### Proof of Concept

1. Protocol deploys `ContractOwner` with `deployer = 0xDEPLOYER`.
2. Deployer key is lost (hardware failure, personnel change, etc.).
3. Owner (multisig) attempts to add a new trading product — calls `addOrUpdateProducts()`.
4. `rawSpotAddOrUpdateProductCalls` is empty because only `deployer` can call `submitSpotAddOrUpdateProductCall()`.
5. `addOrUpdateProducts()` loops over an empty array and does nothing — no product is ever added.
6. No path exists on-chain to recover: the `deployer` variable is immutable post-initialization, and the owner has no function to override it. [9](#0-8)

### Citations

**File:** core/contracts/ContractOwner.sol (L26-26)
```text
    address internal deployer;
```

**File:** core/contracts/ContractOwner.sol (L58-61)
```text
        require(_deployer == msg.sender, "expected deployed to initialize");
        __Ownable_init();
        transferOwnership(multisig);
        deployer = _deployer;
```

**File:** core/contracts/ContractOwner.sol (L70-73)
```text
    modifier onlyDeployer() {
        require(msg.sender == deployer, "sender must be deployer");
        _;
    }
```

**File:** core/contracts/ContractOwner.sol (L91-98)
```text
    function submitSpotAddOrUpdateProductCall(
        uint32 productId,
        uint32 quoteId,
        int128 sizeIncrement,
        int128 minSize,
        ISpotEngine.Config calldata config,
        RiskHelper.RiskStore calldata riskStore
    ) external onlyDeployer {
```

**File:** core/contracts/ContractOwner.sol (L147-182)
```text
    function addOrUpdateProducts(
        uint32[] memory spotIds,
        uint32[] memory perpIds
    ) external onlyOwner {
        for (uint256 i = 0; i < rawSpotAddOrUpdateProductCalls.length; i++) {
            SpotAddOrUpdateProductCall memory call = abi.decode(
                rawSpotAddOrUpdateProductCalls[i],
                (SpotAddOrUpdateProductCall)
            );
            require(spotIds[i] == call.productId, "spot mismatch");
            spotEngine.addOrUpdateProduct(
                call.productId,
                call.quoteId,
                call.sizeIncrement,
                call.minSize,
                call.config,
                call.riskStore
            );
        }
        delete rawSpotAddOrUpdateProductCalls;

        for (uint256 i = 0; i < rawPerpAddOrUpdateProductCalls.length; i++) {
            PerpAddOrUpdateProductCall memory call = abi.decode(
                rawPerpAddOrUpdateProductCalls[i],
                (PerpAddOrUpdateProductCall)
            );
            require(perpIds[i] == call.productId, "perp mismatch");
            perpEngine.addOrUpdateProduct(
                call.productId,
                call.sizeIncrement,
                call.minSize,
                call.riskStore
            );
        }
        delete rawPerpAddOrUpdateProductCalls;
    }
```

**File:** core/contracts/ContractOwner.sol (L361-365)
```text
    function delistProduct(
        uint32[] calldata productIds,
        int128[] calldata pricesX18,
        bytes32[] calldata subaccounts
    ) external onlyDeployer {
```

**File:** core/contracts/BaseProxyManager.sol (L181-183)
```text
    function updateSubmitter(address newSubmitter) external onlyOwner {
        submitter = newSubmitter;
    }
```

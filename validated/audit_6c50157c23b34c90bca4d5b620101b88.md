The full `Airdrop.sol` source is available. Let me trace the exact execution path.

---

### Title
Replay Guard Broken for Zero-Amount Merkle Leaves — (`core/contracts/Airdrop.sol`)

### Summary

`_verifyProof` uses `claimed[week][sender] == 0` as its sole replay sentinel, then unconditionally writes `claimed[week][sender] = totalAmount`. When `totalAmount == 0`, the write is a no-op: the sentinel is never flipped, so the guard passes on every subsequent call. Any address whose leaf encodes `totalAmount = 0` can call `claim()` an unbounded number of times for the same week.

---

### Finding Description

The replay guard and the state-update are on adjacent lines: [1](#0-0) 

```solidity
require(claimed[week][sender] == 0, "Already claimed.");
``` [2](#0-1) 

```solidity
claimed[week][sender] = totalAmount;
```

When `totalAmount == 0`, line 62 writes `0` into a slot that already holds `0`. The storage value is unchanged. On the next call the `require` on line 48 sees `0` again and passes. This cycle repeats without bound.

The downstream transfer and event emission also execute on every iteration: [3](#0-2) 

```solidity
SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount); // transfers 0
emit Claim(msg.sender, week, totalAmount);                       // emitted every time
```

---

### Impact Explanation

- **Broken invariant:** after any successful `claim`, `claimed[week][sender]` must be non-zero. With `totalAmount = 0` this invariant never holds.
- **Unbounded `Claim` event emission:** any off-chain system (indexer, rewards tracker, governance snapshot) that aggregates `Claim` events and trusts their cumulative sum will over-count the attacker's claimed amount by an arbitrary multiplier.
- **No token cost to the attacker:** each iteration transfers 0 tokens, so the attack is free to repeat indefinitely.
- **Persistent broken state:** `claimed[week][sender]` stays `0` forever, so the slot can never be used to correctly record a legitimate future claim for that address/week.

---

### Likelihood Explanation

The precondition is that the merkle tree registered by the owner contains a leaf `(attacker_address, 0)`. The contract imposes no lower-bound check on `totalAmount` anywhere: [4](#0-3) 

A zero-amount leaf can appear through an off-chain data-pipeline bug, a deliberate inclusion of "placeholder" addresses, or a griefing scenario where the attacker controls the leaf construction input. Once such a root is registered, the vulnerability is fully exploitable by any EOA holding the corresponding proof.

---

### Recommendation

Add an explicit non-zero guard before the replay check, or change the sentinel to a dedicated boolean:

```solidity
// Option A – reject zero-amount claims at entry
require(totalAmount > 0, "Zero amount.");

// Option B – use a separate boolean sentinel
mapping(uint32 => mapping(address => bool)) internal hasClaimed;
require(!hasClaimed[week][sender], "Already claimed.");
hasClaimed[week][sender] = true;
claimed[week][sender] = totalAmount;
```

Either fix closes the loop independently of the leaf value.

---

### Proof of Concept

```js
// Hardhat test sketch
const leaf = keccak256(keccak256(abi.encode(attacker.address, 0)));
// build single-leaf tree, register root
await airdrop.connect(owner).registerMerkleRoot(1, root);

for (let i = 0; i < 100; i++) {
    await airdrop.connect(attacker).claim([{ week: 1, totalAmount: 0, proof: [] }]);
    const c = await airdrop.getClaimed(attacker.address);
    assert.equal(c[1].toString(), "0"); // sentinel never flipped
}
// 100 Claim events emitted, all with amount=0, claimed[1][attacker] still 0
``` [5](#0-4)

### Citations

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

**File:** core/contracts/Airdrop.sol (L71-72)
```text
        SafeERC20.safeTransfer(IERC20(token), msg.sender, totalAmount);
        emit Claim(msg.sender, week, totalAmount);
```

**File:** core/contracts/Airdrop.sol (L75-83)
```text
    function claim(ClaimProof[] calldata claimProofs) external {
        for (uint32 i = 0; i < claimProofs.length; i++) {
            _claim(
                claimProofs[i].week,
                claimProofs[i].totalAmount,
                claimProofs[i].proof
            );
        }
    }
```

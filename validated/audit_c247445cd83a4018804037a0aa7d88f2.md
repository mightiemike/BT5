### Title
Unchecked `transferFrom` Return Value in `replaceUsdcEWithUsdc` Enables usdcE Theft — (File: `core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner::replaceUsdcEWithUsdc` performs a raw `IERC20Base.transferFrom` call without checking its return value. If the USDC token at the hardcoded address on chain 57073 (Ink) returns `false` on failure instead of reverting — a known non-standard ERC20 behavior — any unprivileged caller can drain usdcE from any `DirectDepositV1` contract without providing USDC in exchange.

---

### Finding Description

`replaceUsdcEWithUsdc` is an `external` function with no access-control modifier (only a chain-ID gate). Its critical transfer step at line 616 uses a bare interface call:

```solidity
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
```

The return value is silently discarded. The rest of the function then unconditionally:
1. Calls `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — pulling all usdcE into `ContractOwner`.
2. Calls `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — sending that usdcE to the caller.

If the `transferFrom` in step 1 returns `false` without reverting, no USDC is ever deposited, yet the caller receives the full usdcE balance.

The rest of the Nado codebase consistently uses `ERC20Helper.safeTransferFrom` (which low-level-calls the token and asserts `success && (data.length == 0 || abi.decode(data, (bool)))`). This one call site is the sole deviation. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

**High.** A successful exploit drains usdcE tokens held in any `DirectDepositV1` contract associated with any subaccount on chain 57073. The attacker receives real usdcE without providing any USDC. The corrupted asset delta is: `directDepositV1.usdcE balance → 0`, `attacker.usdcE balance += balance`, with zero USDC deposited.

---

### Likelihood Explanation

**Low.** The exploitability depends on the USDC token at `0x2D270e6886d130D724215A266106e6832161EAEd` on Ink chain (57073) returning `false` on a failed transfer rather than reverting. Circle's canonical USDC implementations revert on failure. However, the code pattern is concretely wrong — the return value is never checked — and the function is callable by any unprivileged user with no further barriers. [3](#0-2) 

---

### Recommendation

Replace the raw `transferFrom` call with the project's own `ERC20Helper.safeTransferFrom` wrapper, consistent with every other transfer site in the codebase:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
```

`ERC20Helper` is already imported and `using ERC20Helper for IERC20Base` is already declared in `ContractOwner`. [4](#0-3) 

---

### Proof of Concept

1. Attacker identifies a `subaccount` whose `directDepositV1Address` holds a non-zero usdcE balance.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(subaccount)` on chain 57073 with **zero USDC allowance** granted to `ContractOwner`.
3. `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` returns `false` (non-standard token behavior) — no revert, no USDC moved.
4. `DirectDepositV1(directDepositV1).withdraw(usdcE)` executes, pulling all usdcE into `ContractOwner`.
5. `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` sends the full usdcE balance to the attacker.
6. Attacker receives usdcE for free; the `directDepositV1` vault is emptied. [5](#0-4)

### Citations

**File:** core/contracts/ContractOwner.sol (L18-24)
```text
import "./libraries/ERC20Helper.sol";
import "./common/Constants.sol";

contract ContractOwner is EIP712Upgradeable, OwnableUpgradeable {
    error InvalidInput();
    using MathSD21x18 for int128;
    using ERC20Helper for IERC20Base;
```

**File:** core/contracts/ContractOwner.sol (L608-620)
```text
    function replaceUsdcEWithUsdc(bytes32 subaccount) external {
        require(block.chainid == 57073, ERR_UNAUTHORIZED);
        address payable directDepositV1 = directDepositV1Address[subaccount];
        require(directDepositV1 != address(0), "no dda");
        address usdcE = 0xF1815bd50389c46847f0Bda824eC8da914045D14;
        address usdc = 0x2D270e6886d130D724215A266106e6832161EAEd;
        uint256 balance = IERC20Base(usdcE).balanceOf(directDepositV1);
        if (balance > 0) {
            IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);
            DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE));
            IERC20Base(usdcE).safeTransfer(msg.sender, balance);
        }
    }
```

**File:** core/contracts/libraries/ERC20Helper.sol (L23-42)
```text
    function safeTransferFrom(
        IERC20Base self,
        address from,
        address to,
        uint256 amount
    ) internal {
        (bool success, bytes memory data) = address(self).call(
            abi.encodeWithSelector(
                IERC20Base.transferFrom.selector,
                from,
                to,
                amount
            )
        );

        require(
            success && (data.length == 0 || abi.decode(data, (bool))),
            ERR_TRANSFER_FAILED
        );
    }
```

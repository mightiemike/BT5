### Title
Unchecked Return Value on `transferFrom` in `replaceUsdcEWithUsdc` Enables usdcE Drain Without USDC Payment — (`File: core/contracts/ContractOwner.sol`)

---

### Summary

`ContractOwner.replaceUsdcEWithUsdc` calls `IERC20Base(usdc).transferFrom(...)` directly without checking its boolean return value. The rest of the codebase consistently uses `ERC20Helper.safeTransferFrom` for this purpose. If the raw `transferFrom` silently returns `false` (no-op), execution continues and the caller receives usdcE from the `DirectDepositV1` contract without having provided any USDC.

---

### Finding Description

`ContractOwner` declares `using ERC20Helper for IERC20Base` at line 24, giving every `IERC20Base` instance access to `safeTransfer` and `safeTransferFrom`. These wrappers use a low-level `call` and `require` the return value to be either absent or `true`, matching the pattern used everywhere else in the protocol. [1](#0-0) 

`ERC20Helper.safeTransferFrom` enforces the return value: [2](#0-1) 

However, `replaceUsdcEWithUsdc` at line 616 calls `transferFrom` directly on the raw `IERC20Base` interface, discarding the `bool` return: [3](#0-2) 

The three-step sequence inside the `if (balance > 0)` block is:

1. **Line 616** — `IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance)` — return value ignored.
2. **Line 617** — `DirectDepositV1(directDepositV1).withdraw(IIERC20Base(usdcE))` — unconditionally pulls all usdcE out of the DDA into `ContractOwner`.
3. **Line 618** — `IERC20Base(usdcE).safeTransfer(msg.sender, balance)` — unconditionally sends usdcE to the caller.

If step 1 silently fails (returns `false` without reverting), steps 2 and 3 still execute. The caller receives usdcE without having transferred any USDC.

The inconsistency is stark: line 618 on the very next line correctly uses `safeTransfer`, while line 616 uses the bare `transferFrom`.

---

### Impact Explanation

Any caller who has zero USDC allowance (or whose `transferFrom` call returns `false` for any reason) can drain the usdcE balance of any `DirectDepositV1` contract that has been registered for a `subaccount`. The usdcE is real collateral belonging to the subaccount owner. The attacker receives it for free; the subaccount's DDA is left empty, permanently breaking the migration path for that subaccount.

The `DirectDepositV1.withdraw` call transfers the full usdcE balance to `ContractOwner`: [4](#0-3) 

After which `safeTransfer` forwards it to `msg.sender` with no precondition on whether step 1 succeeded.

---

### Likelihood Explanation

The function has no access control — it is `external` with no `onlyOwner` or similar modifier, callable by any address on Ink chain (chainid 57073). The USDC address `0x2D270e6886d130D724215A266106e6832161EAEd` is a chain-specific deployment whose exact return-value behavior is not guaranteed to match Circle's canonical USDC. Even with standard USDC, a caller with no prior approval simply needs the `transferFrom` to return `false` rather than revert — a behavior exhibited by several widely-deployed ERC20 tokens — to trigger the drain. [5](#0-4) 

---

### Recommendation

Replace the bare `transferFrom` call with the `safeTransferFrom` wrapper already available via `ERC20Helper`, consistent with every other token transfer in the codebase:

```solidity
// Before (line 616):
IERC20Base(usdc).transferFrom(msg.sender, directDepositV1, balance);

// After:
IERC20Base(usdc).safeTransferFrom(msg.sender, directDepositV1, balance);
``` [2](#0-1) 

---

### Proof of Concept

1. A `DirectDepositV1` exists for `subaccount S` on Ink chain (chainid 57073) holding `N` usdcE tokens.
2. Attacker calls `ContractOwner.replaceUsdcEWithUsdc(S)` with **zero USDC allowance** granted to `ContractOwner`.
3. Line 616 executes `IERC20Base(usdc).transferFrom(attacker, directDepositV1, N)`. Because the attacker has no allowance, this returns `false` (no revert) on a non-reverting ERC20.
4. The return value is discarded; execution continues.
5. Line 617 calls `DirectDepositV1(directDepositV1).withdraw(usdcE)`, which transfers all `N` usdcE to `ContractOwner`.
6. Line 618 calls `IERC20Base(usdcE).safeTransfer(attacker, N)`, sending `N` usdcE to the attacker.
7. The attacker has received `N` usdcE at zero cost. The DDA for subaccount `S` is empty. [6](#0-5)

### Citations

**File:** core/contracts/ContractOwner.sol (L24-24)
```text
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

**File:** core/contracts/DirectDepositV1.sol (L103-106)
```text
    function withdraw(IIERC20Base token) external onlyOwner {
        uint256 balance = token.balanceOf(address(this));
        safeTransfer(token, msg.sender, balance);
    }
```
